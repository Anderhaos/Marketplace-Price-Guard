"""Read-only seller can configure locally without creating WB write intents."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant
from guard.models import GuardAccount, Product, Upload


@override_settings(
    SAAS_WB_READ_ONLY=True, SAAS_WB_NETWORK_ENABLED=True,
    SAAS_PIN_AUTH_ENABLED=True, SAAS_ACCESS_ENFORCED=True,
)
class LocalPolicyEditorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="readonly-policy@example.test", email="readonly-policy@example.test", password="unused"
        )
        self.account = GuardAccount.objects.create(
            user=self.user, name="WB", marketplace="wb", token_ciphertext="fake",
            initial_sync_completed_at=timezone.now(),
        )
        self.product = Product.objects.create(
            account=self.account, nm_id=12020, title="One", current_discount=13,
            price=100, discounted_price=87,
        )
        AccessGrant.objects.create(
            user=self.user, source="admin", source_ref="local-policy-test",
            starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1),
        )
        self.client.force_login(self.user)

    def test_local_limit_saved_without_worker_or_pending_wb_write(self):
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, 'name="action" value="save_limit"')
        self.assertContains(page, 'Сохранить предел только в NoSale')
        with patch("guard.views._check_now", side_effect=AssertionError("WB send forbidden")):
            response = self.client.post(
                reverse("dashboard"),
                {"action": "save_limit", "default_allowed_discount": "13"},
                follow=True,
            )
        self.assertEqual(response.status_code, 200)
        self.account.refresh_from_db()
        self.assertEqual(self.account.default_allowed_discount, 13)
        self.assertFalse(self.account.enabled)
        self.assertFalse(self.account.apply_all_requested)
        self.assertEqual(self.account.apply_requested_ids, [])
        self.assertEqual(Upload.objects.count(), 0)
        self.assertContains(response, "Предел 13% сохранён только в NoSale")

    def test_readonly_card_rule_never_enqueues_wb_change(self):
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, 'action="' + reverse("product_rule", args=[12020]) + '"')
        with patch("guard.views._check_now", side_effect=AssertionError("WB send forbidden")):
            resp = self.client.post(reverse("product_rule", args=[12020]), {"no_discount": "on"})
        self.assertEqual(resp.status_code, 302)
        self.product.refresh_from_db()
        self.account.refresh_from_db()
        self.assertEqual(self.product.allowed_discount_override, 0)
        self.assertFalse(self.account.enabled)
        self.assertEqual(self.account.apply_requested_ids, [])
        self.assertFalse(self.account.apply_all_requested)
        self.assertEqual(Upload.objects.count(), 0)

    def test_readonly_write_actions_still_rejected(self):
        self.client.post(reverse("dashboard"), {"action": "toggle_autocontrol", "enabled": "on"})
        self.client.post(reverse("dashboard"), {
            "action": "apply_all", "default_allowed_discount": "5"
        })
        self.account.refresh_from_db()
        self.assertFalse(self.account.enabled)
        self.assertFalse(self.account.apply_all_requested)
        self.assertEqual(self.account.default_allowed_discount, 0)

    def test_limit_invalid_or_zero_without_confirmation_cannot_save(self):
        for value in ("-1", "100", "0", "bad"):
            self.client.post(reverse("dashboard"), {
                "action": "save_limit", "default_allowed_discount": value
            })
            self.account.refresh_from_db()
            self.assertEqual(self.account.default_allowed_discount, 0)

    def test_expired_user_cannot_modify_readonly_rules(self):
        AccessGrant.objects.filter(user=self.user).update(ends_at=timezone.now()-timedelta(seconds=1))
        self.client.post(reverse("dashboard"), {
            "action": "save_limit", "default_allowed_discount": "25"
        })
        self.client.post(reverse("product_rule", args=[12020]), {"no_discount": "on"})
        self.account.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.account.default_allowed_discount, 0)
        self.assertIsNone(self.product.allowed_discount_override)

    def test_other_tenant_rule_access_is_not_allowed(self):
        another = User.objects.create_user(username="other-policy@example.test")
        other_account = GuardAccount.objects.create(
            user=another, name="Other WB", token_ciphertext="fake",
            initial_sync_completed_at=timezone.now(),
        )
        item = Product.objects.create(account=other_account, nm_id=23450, title="Other")
        self.assertEqual(self.client.post(reverse("product_rule", args=[item.nm_id]), {
            "no_discount": "on"
        }).status_code, 404)
        item.refresh_from_db()
        self.assertIsNone(item.allowed_discount_override)
