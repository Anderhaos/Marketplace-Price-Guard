"""Release safety: no accidental WB token collection or discount writes."""
import os
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse

from guard.models import GuardAccount
from guard.wb import WBClient, WBApiError


class SafeSettingsDefaultTests(SimpleTestCase):
    def test_readonly_default_is_true_when_env_missing(self):
        from portal.settings import env_bool
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(env_bool("SAAS_WB_READ_ONLY", True))


@override_settings(SAAS_PIN_AUTH_ENABLED=True, SAAS_WB_NETWORK_ENABLED=False,
                   SAAS_WB_TOKEN_VALIDATION_ENABLED=True)
class DisabledWBOnboardingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("locked@example.test", password="1234")
        self.client.force_login(self.user)

    def test_dashboard_explicitly_blocks_token_entry_during_shutdown(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Подключение Wildberries временно закрыто")
        self.assertContains(response, 'id="onboarding-token"')
        html = response.content.decode()
        self.assertIn('name="token" ', html)
        self.assertIn('disabled maxlength="4096"', html)
        self.assertNotIn('type="submit" class="setup-submit">Подключить магазин', html)

    def test_post_remains_blocked_even_if_ui_is_bypassed(self):
        with patch("guard.integrations.WBClient") as client:
            r = self.client.post(reverse("connect_wb"), {"token": "synthetic.jwt.token"}, follow=True)
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "Подключение WB API отключено")
            client.assert_not_called()
        self.assertFalse(GuardAccount.objects.filter(user=self.user, token_ciphertext__gt="").exists())

    @override_settings(SAAS_WB_NETWORK_ENABLED=True, SAAS_WB_TOKEN_VALIDATION_ENABLED=True)
    def test_allowed_preview_still_shows_normal_token_entry(self):
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, 'class="setup-submit" >Подключить магазин')
        self.assertNotContains(r, "Подключение Wildberries временно закрыто")
        self.assertNotContains(r, 'disabled maxlength="4096"')


@override_settings(
    SAAS_PIN_AUTH_ENABLED=True,
    REQUIRE_EMAIL_VERIFICATION=True,
    SAAS_WB_NETWORK_ENABLED=True,
    SAAS_WB_READ_ONLY=True,
    SAAS_WB_TOKEN_VALIDATION_ENABLED=True,
    SAAS_ACCESS_ENFORCED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    TOCHKA_BILLING_ENABLED=True,
    NOSALE_MONTHLY_PRICE_RUB="500",
    NOSALE_STANDARD_MONTHLY_PRICE_RUB="500",
    NOSALE_SMART_MONTHLY_PRICE_RUB="990",
    TOCHKA_MERCHANT_ID="test-merchant-not-production",
    TOCHKA_CUSTOMER_CODE="test-customer-not-production",
)
class OneSellerReleaseJourneyTests(TestCase):
    """One synthetic seller only, full DB transaction rollback after test."""

    def test_registration_wb_trial_two_tariffs_and_refund_without_external_io(self):
        import base64
        import json
        import re
        from django.core import mail
        from django.utils import timezone
        from billing.access import effective_plan, has_plan_capability
        from billing.models import AccessGrant, SubscriptionPayment
        from billing.service import create_checkout, apply_verified_event
        from guard.models import GuardAccount
        from datetime import timedelta

        email = "journey-test@example.test"
        start = self.client.post("/login/", {"email": email})
        self.assertEqual(start.status_code, 302)
        self.assertEqual(start["Location"], "/auth/code/")
        self.assertEqual(len(mail.outbox), 1)
        code = re.search(r"Код: (\d{4})", mail.outbox[0].body)
        self.assertIsNotNone(code)
        self.assertEqual(self.client.post("/auth/code/", {"code": code.group(1)}).status_code, 302)
        pin = self.client.post("/auth/pin/", {"pin": "8372", "confirm_pin": "8372"})
        self.assertEqual(pin.status_code, 302)
        seller = User.objects.get(email=email)
        self.assertFalse(seller.is_superuser)
        self.assertEqual(GuardAccount.objects.filter(user=seller).count(), 1)

        payload = base64.urlsafe_b64encode(
            json.dumps({"acc": 4, "s": (1 << 1) | (1 << 3)}).encode()
        ).decode().rstrip("=")
        fake_token = "header." + payload + ".signature"
        with patch("guard.integrations.WBClient") as wb:
            result = self.client.post(reverse("connect_wb"), {"token": fake_token})
            self.assertEqual(result.status_code, 302)
            wb.return_value.validate_token.assert_called_once()
        account = GuardAccount.objects.get(user=seller)
        self.assertTrue(account.token_ciphertext)
        self.assertFalse(account.enabled)
        self.assertTrue(account.check_requested)
        self.assertIsNone(effective_plan(seller.pk))
        self.assertFalse(AccessGrant.objects.filter(user=seller, source="trial").exists())
        # A real first run waits for complete read-only inventory evidence;
        # mock only WB HTTP, not the worker or the trial ledger.
        from guard.monitor import run_due_checks
        with patch("guard.monitor.WBClient") as read_client:
            read_client.return_value.get_product_cards.return_value = [{
                "nmID": 5101, "title": "Synthetic seller item",
            }]
            read_client.return_value.get_products.return_value = [{
                "nmID": 5101, "discount": 15, "sizes": [
                    {"price": 1000, "discountedPrice": 850},
                ],
            }]
            self.assertEqual(run_due_checks(), 1)
            read_client.return_value.submit_discounts.assert_not_called()
        account.refresh_from_db()
        self.assertIsNotNone(account.initial_sync_completed_at)
        self.assertEqual(effective_plan(seller.pk), "standard")
        self.assertFalse(has_plan_capability(seller.pk, "ai_questions"))
        self.assertEqual(AccessGrant.objects.filter(user=seller, source="trial").count(), 1)

        with patch("billing.service.create_tochka_link",
                   side_effect=lambda payment: ("https://merch.tochka.com/order/fixture",
                                                "fake-operation-" + str(payment.pk))):
            standard = create_checkout(seller.pk, plan="standard")
            smart = create_checkout(seller.pk, plan="smart")
        self.assertEqual(standard.plan, "standard")
        self.assertEqual(smart.plan, "smart")
        self.assertEqual(SubscriptionPayment.objects.filter(user=seller).count(), 2)
        self.assertEqual((standard.amount_kopeks, smart.amount_kopeks), (50000, 99000))

        def event(pay, status="APPROVED"):
            return {"data": {
                "webhookType": "acquiringInternetPayment",
                "operationId": pay.operation_id, "paymentLinkId": str(pay.pk),
                "merchantId": "test-merchant-not-production",
                "customerCode": "test-customer-not-production",
                "amount": str(pay.amount_rubles), "status": status,
            }}
        self.assertEqual(apply_verified_event(event(standard)), "activated")
        self.assertEqual(apply_verified_event(event(smart)), "activated")
        self.assertEqual(apply_verified_event(event(smart)), "duplicate")
        self.assertEqual(effective_plan(seller.pk), "standard")
        smart_grant = AccessGrant.objects.get(source_ref=f"nosale:tochka:{smart.pk}")
        self.assertGreater(smart_grant.starts_at, timezone.now())
        self.assertEqual(effective_plan(seller.pk, at=smart_grant.starts_at + timedelta(seconds=1)), "smart")
        self.assertEqual(apply_verified_event(event(smart, "REFUNDED")), "refunded")
        smart_grant.refresh_from_db()
        self.assertIsNotNone(smart_grant.revoked_at)
        self.assertNotEqual(effective_plan(seller.pk, at=smart_grant.starts_at + timedelta(seconds=1)), "smart")
        # No real bank request, seller WB HTTP, AI, or unintended price upload occurred.
