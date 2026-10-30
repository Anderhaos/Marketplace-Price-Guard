"""Smart change briefs: bounded, opt-in, tenant-scoped, and idempotent."""
from datetime import datetime, timedelta, timezone as dt_timezone

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant, OwnerAISettings, OwnerOperationsSettings
from guard.models import GuardAccount, GuardEvent, Product
from .models import NotificationPreference, PushMessage
from .service import create_smart_briefs, process_notification_cycle


class SmartBriefTests(TestCase):
    # 08:05 and 20:05 Europe/Moscow; keep all slots deterministic.
    morning = datetime(2026, 10, 30, 5, 5, tzinfo=dt_timezone.utc)
    evening = datetime(2026, 10, 30, 17, 5, tzinfo=dt_timezone.utc)

    def setUp(self):
        OwnerOperationsSettings.objects.create()
        OwnerAISettings.objects.create(smart_briefs_per_day=2, ai_enabled=False)
        self.owner = User.objects.create_user("brief@example.test", "brief@example.test", "pass")
        NotificationPreference.objects.create(
            user=self.owner, daily_enabled=True, daily_hour=23, timezone_name="Europe/Moscow",
        )
        AccessGrant.objects.create(
            user=self.owner, source="admin", source_ref="brief-smart-grant", plan="smart",
            starts_at=self.morning-timedelta(days=2), ends_at=self.evening+timedelta(days=2),
        )
        self.store = GuardAccount.objects.create(
            user=self.owner, marketplace="wb", name="Мой WB",
            token_ciphertext="opaque-test", default_allowed_discount=10,
            initial_sync_completed_at=self.morning, last_check_at=self.morning,
        )
        Product.objects.create(
            account=self.store, nm_id=123, current_discount=18, checked_at=self.morning,
        )

    def event(self, kind, when=None, *, store=None):
        event = GuardEvent.objects.create(
            account=store or self.store, kind=kind, message="fixture",
        )
        GuardEvent.objects.filter(pk=event.pk).update(
            created_at=when or self.morning - timedelta(minutes=20),
        )
        return event

    def test_two_local_slots_only_when_events_change_and_no_duplicate(self):
        self.event("discount_changed")
        self.event("check")
        self.assertEqual(create_smart_briefs(now=self.morning), 1)
        first = PushMessage.objects.get(kind="smart")
        self.assertEqual(first.smart_slot, "2026-10-30:08")
        self.assertIn("изменений цен/скидок — 1", first.body)
        self.assertIn("WB 123", first.body)
        self.assertEqual(first.url, "/advisor/")
        self.assertEqual(create_smart_briefs(now=self.morning+timedelta(minutes=1)), 0)
        # No evening update: previous morning change must not be repeated.
        self.assertEqual(create_smart_briefs(now=self.evening), 0)
        self.event("rule", self.evening-timedelta(minutes=2))
        self.assertEqual(create_smart_briefs(now=self.evening), 1)
        self.assertEqual(create_smart_briefs(now=self.evening), 0)
        messages = PushMessage.objects.filter(user=self.owner, kind="smart")
        self.assertEqual(messages.count(), 2)
        self.assertEqual(set(messages.values_list("smart_slot", flat=True)),
                         {"2026-10-30:08", "2026-10-30:20"})
        self.assertEqual(sum(m.body.count("правил NoSale — 1") for m in messages), 1)

    def test_one_per_day_only_evening_and_zero_disables(self):
        settings = OwnerAISettings.objects.get()
        settings.smart_briefs_per_day = 1
        settings.save()
        self.event("price_changed", self.evening-timedelta(minutes=10))
        self.assertEqual(create_smart_briefs(now=self.morning), 0)
        self.assertEqual(create_smart_briefs(now=self.evening), 1)
        settings.smart_briefs_per_day = 0
        settings.save()
        self.assertEqual(create_smart_briefs(now=self.evening), 0)

    def test_no_updates_no_briefs_and_no_catch_up_replay(self):
        self.event("check")
        self.assertEqual(create_smart_briefs(now=self.morning), 0)
        self.event("confirmed", self.morning-timedelta(minutes=2))
        self.assertEqual(create_smart_briefs(now=self.morning+timedelta(hours=3)), 0)
        self.assertEqual(PushMessage.objects.filter(kind="smart").count(), 0)

    def test_preferences_owner_pause_and_expired_grant_fail_closed(self):
        self.event("error")
        pref = NotificationPreference.objects.get(user=self.owner)
        pref.daily_enabled = False
        pref.save()
        self.assertEqual(create_smart_briefs(now=self.morning), 0)
        pref.daily_enabled = True
        pref.save()
        policy = OwnerOperationsSettings.objects.get()
        policy.daily_digests_enabled = False
        policy.save()
        self.assertEqual(create_smart_briefs(now=self.morning), 0)
        policy.daily_digests_enabled = True
        policy.save()
        AccessGrant.objects.filter(user=self.owner).update(revoked_at=self.morning)
        self.assertEqual(create_smart_briefs(now=self.morning), 0)

    def test_other_seller_data_not_leaked(self):
        other = User.objects.create_user("other@example.test", "other@example.test", "pass")
        foreign = GuardAccount.objects.create(
            user=other, marketplace="wb", name="Чужой секрет",
            token_ciphertext="foreign-token",
        )
        self.event("discount_changed", store=foreign)
        self.assertEqual(create_smart_briefs(now=self.morning), 0)
        self.event("rule")
        self.assertEqual(create_smart_briefs(now=self.morning), 1)
        message = PushMessage.objects.get(kind="smart")
        self.assertNotIn("Чужой секрет", message.body)
        self.assertNotIn("foreign-token", message.body)

    def test_notifications_ui_and_unread_count(self):
        self.event("discount_changed")
        self.assertEqual(create_smart_briefs(now=self.morning), 1)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("notifications"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "NoSale Smart — изменения")
        self.assertGreaterEqual(response.context["unread_notification_count"], 1)
        self.client.post(reverse("mark_notifications_read"))
        self.assertFalse(PushMessage.objects.get(kind="smart").read_at is None)

    def test_missing_owner_ai_settings_fails_closed_without_model_calls(self):
        OwnerAISettings.objects.all().delete()
        self.event("discount_changed")
        self.assertEqual(create_smart_briefs(now=self.morning), 0)
        self.assertEqual(PushMessage.objects.filter(kind="smart").count(), 0)
