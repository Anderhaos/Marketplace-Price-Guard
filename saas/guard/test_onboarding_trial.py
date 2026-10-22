import base64
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.access import has_active_access
from billing.models import AccessGrant
from billing.trial import TRIAL_DAYS, grant_trial_once, trial_source_ref, access_snapshot
from .models import GuardAccount


def token_for(kind=4, scopes=(1 << 1) | (1 << 3)):
    payload = base64.urlsafe_b64encode(json.dumps({"acc": kind, "s": scopes}).encode()).decode().rstrip("=")
    return "header." + payload + ".signature"


@override_settings(
    SAAS_PIN_AUTH_ENABLED=True,
    SAAS_WB_TOKEN_VALIDATION_ENABLED=True, SAAS_WB_NETWORK_ENABLED=False,
    SAAS_ACCESS_ENFORCED=True,
)
class FirstRunOnboardingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("onboard@example.org", "onboard@example.org", "1234")
        self.client.force_login(self.user)

    def test_initial_dashboard_is_modal_only_and_has_token_input(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="wb-setup"')
        self.assertContains(response, 'name="token"')
        self.assertContains(response, "5 дней бесплатно")
        self.assertNotContains(response, "Всего товаров")
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())

    @patch("guard.integrations.WBClient")
    def test_validate_issues_one_five_day_trial_without_autocorrect(self, mock_wb):
        account = GuardAccount.objects.create(user=self.user, name="Первый")
        response = self.client.post(reverse("connect_wb"), {"token": token_for()})
        self.assertRedirects(response, reverse("dashboard"))
        mock_wb.return_value.validate_token.assert_called_once()
        account.refresh_from_db()
        self.assertTrue(account.token_ciphertext)
        self.assertFalse(account.enabled)
        self.assertTrue(account.check_requested)
        grant = AccessGrant.objects.get(user=self.user, source="trial")
        self.assertEqual(grant.source_ref, trial_source_ref(self.user.pk))
        self.assertAlmostEqual((grant.ends_at-grant.starts_at).total_seconds(),TRIAL_DAYS*86400,delta=1)
        self.assertTrue(has_active_access(self.user.pk))
        self.assertNotContains(self.client.get(reverse("dashboard")), 'id="wb-setup"')

    @patch("guard.integrations.WBClient")
    def test_trial_does_not_restart_for_reconnect_or_another_store(self, mock_wb):
        account = GuardAccount.objects.create(user=self.user)
        self.client.post(reverse("connect_wb"), {"token": token_for()})
        grant = AccessGrant.objects.get(user=self.user)
        then = grant.ends_at
        self.client.post(reverse("connect_wb"), {"token": token_for()})
        account_two = GuardAccount.objects.create(user=self.user, name="Второй")
        session = self.client.session
        session["mpno_selected_store_id"] = account_two.pk
        session.save()
        self.client.post(reverse("connect_wb"), {"token": token_for()})
        self.assertEqual(AccessGrant.objects.filter(user=self.user, source="trial").count(), 1)
        grant.refresh_from_db()
        self.assertEqual(grant.ends_at, then)

    @patch("guard.integrations.WBClient")
    def test_expired_trial_cannot_be_renewed_by_token_replacement(self, mock_wb):
        grant = AccessGrant.objects.create(user=self.user, source="trial",
            source_ref=trial_source_ref(self.user.pk),
            starts_at=timezone.now()-timedelta(days=10),
            ends_at=timezone.now()-timedelta(days=5))
        self.client.post(reverse("connect_wb"), {"token": token_for()})
        grant.refresh_from_db()
        self.assertFalse(has_active_access(self.user.pk))
        self.assertTrue(access_snapshot(self.user.pk)["trial_expired"])
        self.assertEqual(AccessGrant.objects.filter(user=self.user).count(), 1)

    @patch("guard.integrations.WBClient")
    def test_personal_token_for_cloud_rejected_without_trial(self, mock_wb):
        self.client.post(reverse("connect_wb"), {"token": token_for(kind=3)})
        mock_wb.assert_not_called()
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())

    @patch("guard.integrations.WBClient")
    def test_missing_scope_rejected_without_trial(self, mock_wb):
        self.client.post(reverse("connect_wb"), {"token": token_for(scopes=(1 << 3))})
        mock_wb.assert_not_called()
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())

    @patch("guard.integrations.WBClient")
    def test_readonly_token_rejected_without_trial(self, mock_wb):
        self.client.post(reverse("connect_wb"), {"token": token_for(scopes=(1 << 1) | (1 << 3) | (1 << 30))})
        mock_wb.assert_not_called()
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())

    @patch("guard.integrations.WBClient")
    def test_network_verification_failure_does_not_start_trial(self, mock_wb):
        from .wb import WBApiError
        mock_wb.return_value.validate_token.side_effect=WBApiError("denied", 401)
        self.client.post(reverse("connect_wb"), {"token": token_for()})
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())
        self.assertFalse(GuardAccount.objects.get(user=self.user).token_ciphertext)

    @override_settings(SAAS_WB_TOKEN_VALIDATION_ENABLED=False)
    @patch("guard.integrations.WBClient")
    def test_maintenance_flag_fails_closed_without_trial(self, mock_wb):
        self.client.post(reverse("connect_wb"), {"token": token_for()})
        mock_wb.assert_not_called()
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())

    @patch("guard.integrations.WBClient")
    def test_settings_and_onboarding_share_save_path(self, mock_wb):
        # Settings are intentionally inaccessible before first-run setup.
        self.assertRedirects(self.client.get(reverse("settings")), reverse("dashboard"))
        self.client.post(reverse("connect_wb"), {"token": token_for()})
        self.assertTrue(has_active_access(self.user.pk))
        response = self.client.post(reverse("settings"), {"action":"token", "token":token_for()})
        self.assertRedirects(response, reverse("settings"))
        self.assertEqual(AccessGrant.objects.filter(user=self.user).count(), 1)
        self.assertEqual(mock_wb.return_value.validate_token.call_count, 2)


    @patch("guard.integrations.WBClient")
    def test_token_replacement_discards_old_pending_discount_writes(self, mock_wb):
        from .models import Upload
        account = GuardAccount.objects.create(
            user=self.user, token_ciphertext="old-ciphertext", enabled=True,
            apply_all_requested=True, apply_requested_ids=[1234], rules_version=3,
            lock_until=timezone.now() + timedelta(minutes=10),
        )
        upload = Upload.objects.create(account=account, upload_id=12345, status="pending")
        response = self.client.post(reverse("connect_wb"), {"token": token_for()})
        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        upload.refresh_from_db()
        self.assertFalse(account.enabled)
        self.assertFalse(account.apply_all_requested)
        self.assertEqual(account.apply_requested_ids, [])
        self.assertTrue(account.check_requested)
        self.assertIsNone(account.lock_until)
        self.assertEqual(account.rules_version, 4)
        self.assertEqual(upload.status, "unknown")

    def test_business_pages_redirect_to_onboarding_until_connected(self):
        for path in ("/stores/", "/settings/", "/history/", "/notifications/", "/push/preferences/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertRedirects(response, reverse("dashboard"))

    def test_onboarding_rejection_visible_inside_modal(self):
        response = self.client.post(reverse("connect_wb"), {"token": "not-a-valid-token"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="setup-feedback"')
        self.assertContains(response, "Не удалось определить права токена")
        self.assertNotContains(response, '<ul class="messages"')

    @patch("guard.integrations.WBClient")
    def test_after_connect_business_pages_accessible(self, mock_wb):
        self.client.post(reverse("connect_wb"), {"token": token_for()})
        for path in ("/stores/", "/settings/", "/history/", "/notifications/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    @override_settings(SAAS_PIN_AUTH_ENABLED=False)
    def test_legacy_mode_not_affected_by_onboarding_gate(self):
        self.assertEqual(self.client.get("/settings/").status_code, 200)

    @patch("guard.integrations.WBClient")
    def test_basic_token_connects_read_only_without_price_probe(self, mock_wb):
        self.client.post(reverse("connect_wb"), {"token": token_for(kind=1)})
        mock_wb.return_value.validate_token.assert_called_once_with(probe_prices=False)
        account = GuardAccount.objects.get(user=self.user)
        self.assertTrue(account.token_ciphertext)
        self.assertFalse(account.enabled)
        self.assertTrue(has_active_access(self.user.pk))
        response = self.client.post(reverse("dashboard"), {
            "action": "apply_all", "default_allowed_discount": 12,
        })
        self.assertRedirects(response, reverse("dashboard"))
        account.refresh_from_db()
        self.assertFalse(account.apply_all_requested)

    @override_settings(SAAS_WB_NETWORK_ENABLED=True)
    @patch("guard.integrations.WBClient")
    @patch("guard.monitor.WBClient")
    def test_basic_token_worker_loads_products_without_writing(self, monitor_wb, integration_wb):
        from guard.models import Product
        from guard.monitor import run_due_checks
        self.client.post(reverse("connect_wb"), {"token": token_for(kind=1)})
        mocked = monitor_wb.return_value
        mocked.get_product_cards.return_value = []
        mocked.get_products.return_value = [{
            "nmID": 1472, "vendorCode": "demo", "discount": 25,
            "sizes": [{"price": 100, "discountedPrice": 75}],
        }]
        self.assertEqual(run_due_checks(), 1)
        self.assertTrue(Product.objects.filter(account__user=self.user, nm_id=1472).exists())
        mocked.submit_discounts.assert_not_called()
        account = GuardAccount.objects.get(user=self.user)
        self.assertFalse(account.enabled)
        self.assertTrue(account.check_requested)
        self.assertGreater((account.next_check_at-timezone.now()).total_seconds(), 14*60)
        self.assertEqual(run_due_checks(), 0)

    @patch("guard.integrations.WBClient")
    def test_basic_readonly_token_allowed_for_preview(self, mock_wb):
        limited = token_for(kind=1, scopes=(1 << 1) | (1 << 3) | (1 << 30))
        response = self.client.post(reverse("connect_wb"), {"token": limited})
        self.assertRedirects(response, reverse("dashboard"))
        mock_wb.return_value.validate_token.assert_called_once_with(probe_prices=False)
        self.assertTrue(has_active_access(self.user.pk))
