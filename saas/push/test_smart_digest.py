"""First Smart daily recommendation reuses existing reliable digest outbox."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from billing.models import AccessGrant
from guard.models import GuardAccount, GuardEvent, Product
from .models import NotificationPreference, PushMessage
from .service import create_daily_digests


class SmartDailyDigestTests(TestCase):
    def create_user_store(self, suffix, *, plan):
        user = User.objects.create_user(f"{suffix}@example.test", f"{suffix}@example.test", "pass")
        NotificationPreference.objects.create(user=user, daily_enabled=True, daily_hour=0)
        AccessGrant.objects.create(
            user=user, plan=plan, source="admin", source_ref=f"digest-{suffix}",
            starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1),
        )
        store = GuardAccount.objects.create(
            user=user, name=f"Магазин {suffix}", marketplace="wb",
            token_ciphertext="test-opaque", default_allowed_discount=15,
            last_check_at=timezone.now(), initial_sync_completed_at=timezone.now(),
        )
        Product.objects.create(account=store, nm_id=120, title="Тестовый товар", current_discount=35, checked_at=timezone.now())
        GuardEvent.objects.create(account=store, kind="check", message="WB checked")
        return user

    def test_smart_receives_one_evidence_based_action_no_new_calls(self):
        user = self.create_user_store("smart-digest", plan="smart")
        now = timezone.now()
        self.assertEqual(create_daily_digests(now=now), 1)
        digest = PushMessage.objects.get(user=user)
        self.assertEqual(digest.kind, "digest")
        self.assertIn("NoSale Smart", digest.title)
        self.assertIn("Проверьте товар WB 120", digest.body)
        self.assertEqual(digest.url, "/advisor/")
        self.assertEqual(create_daily_digests(now=now), 0)
        self.assertEqual(PushMessage.objects.filter(user=user).count(), 1)

    def test_standard_keeps_regular_digest_without_smart_marketing(self):
        user = self.create_user_store("standard-digest", plan="standard")
        self.assertEqual(create_daily_digests(now=timezone.now()), 1)
        digest = PushMessage.objects.get(user=user)
        self.assertNotIn("Smart", digest.title)
        self.assertNotIn("Проверьте товар WB", digest.body)
        self.assertEqual(digest.url, "/notifications/")

    def test_digest_skips_opted_out_seller(self):
        user = self.create_user_store("optedout", plan="smart")
        NotificationPreference.objects.filter(user=user).update(daily_enabled=False)
        self.assertEqual(create_daily_digests(now=timezone.now()), 0)
        self.assertFalse(PushMessage.objects.filter(user=user).exists())
