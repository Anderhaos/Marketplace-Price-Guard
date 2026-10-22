from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .access import has_active_access
from .models import AccessGrant
from guard.models import GuardAccount
from guard.monitor import check_account, run_due_checks


@override_settings(SAAS_WB_NETWORK_ENABLED=True, SAAS_ACCESS_ENFORCED=True)
class AccessGateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="seller@example.org", email="seller@example.org", password="test-password-123"
        )
        self.account = GuardAccount.objects.create(
            user=self.user, enabled=True, token_ciphertext="not-a-real-token"
        )

    def grant(self, **changes):
        now = timezone.now()
        data = {
            "user": self.user, "source": "trial", "source_ref": "trial:one",
            "starts_at": now-timedelta(days=1), "ends_at": now+timedelta(days=1),
        }
        data.update(changes)
        return AccessGrant.objects.create(**data)

    @patch("guard.monitor.WBClient")
    def test_no_grant_blocks_direct_and_worker_calls(self, client):
        self.assertFalse(has_active_access(self.user.id))
        self.assertFalse(check_account(self.account))
        self.assertEqual(run_due_checks(), 0)
        client.assert_not_called()

    @patch("guard.monitor.WBClient")
    def test_expired_grant_blocks_marketplace(self, client):
        self.grant(ends_at=timezone.now()-timedelta(seconds=1))
        self.assertFalse(check_account(self.account))
        self.assertEqual(run_due_checks(), 0)
        client.assert_not_called()

    @patch("guard.monitor.WBClient")
    def test_revoked_grant_blocks_marketplace(self, client):
        self.grant(revoked_at=timezone.now())
        self.assertFalse(check_account(self.account))
        client.assert_not_called()

    @patch("guard.monitor.WBClient")
    def test_other_users_grant_is_not_authorization(self, client):
        another = User.objects.create_user("other@example.org", "other@example.org", "test-password-123")
        self.grant(user=another)
        self.assertFalse(check_account(self.account))
        client.assert_not_called()

    @override_settings(SAAS_WB_NETWORK_ENABLED=False)
    @patch("guard.monitor.WBClient")
    def test_network_flag_defaults_to_disabled(self, client):
        self.grant()
        self.assertFalse(check_account(self.account))
        self.assertEqual(run_due_checks(), 0)
        client.assert_not_called()

    def test_unpaid_user_cannot_enable_guard(self):
        self.account.enabled = False
        self.account.save(update_fields=["enabled"])
        self.client.force_login(self.user)
        response = self.client.post(reverse("dashboard"), {"action":"toggle_autocontrol", "enabled":"on"})
        self.assertEqual(response.status_code, 302)
        self.account.refresh_from_db()
        self.assertFalse(self.account.enabled)

    @override_settings(SAAS_WB_TOKEN_VALIDATION_ENABLED=False)
    def test_disabled_token_validation_fails_closed(self):
        self.client.force_login(self.user)
        with patch("guard.integrations.WBClient") as wb:
            result = self.client.post(reverse("settings"), {"action":"token", "token":"fake-token"})
        self.assertEqual(result.status_code, 200)
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())
        wb.assert_not_called()

    @patch("guard.monitor.decrypt_token", return_value="mock-valid-token")
    @patch("guard.monitor.WBClient")
    def test_active_grant_allows_scheduled_read(self, client, decrypt):
        self.grant()
        instance = client.return_value
        instance.get_product_cards.return_value = []
        instance.get_products.return_value = []
        self.assertEqual(run_due_checks(), 1)
        instance.get_products.assert_called_once()
