"""R1: two tariffs, immutable bank quotes and grant entitlements."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .access import effective_plan, has_plan_capability
from .models import AccessGrant, SubscriptionPayment
from .plans import tariff_offers
from .service import apply_verified_event, create_checkout
from .tochka import AcquiringUnavailable
from .trial import access_snapshot


@override_settings(
    TOCHKA_BILLING_ENABLED=True,
    NOSALE_MONTHLY_PRICE_RUB="500.00",
    NOSALE_STANDARD_MONTHLY_PRICE_RUB=None,
    NOSALE_SMART_MONTHLY_PRICE_RUB="990.00",
    TOCHKA_MERCHANT_ID="test-merchant",
    TOCHKA_CUSTOMER_CODE="test-customer",
    SAAS_ACCESS_ENFORCED=True,
    SAAS_PIN_AUTH_ENABLED=False,
)
class TwoTariffTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="two-plan-seller@example.org", email="two-plan-seller@example.org",
            password="test-pass-123",
        )
        cls.other = User.objects.create_user(
            username="other-plan-seller@example.org", email="other-plan-seller@example.org",
            password="test-pass-123",
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.bank_link = patch(
            "billing.service.create_tochka_link",
            side_effect=lambda pay: ("https://merch.tochka.com/order/test", f"op-{pay.pk}"),
        )
        self.bank_link.start()
        self.addCleanup(self.bank_link.stop)

    def bank_event(self, pay, *, amount=None, status="APPROVED", merchant="test-merchant"):
        return {"data": {
            "webhookType": "acquiringInternetPayment",
            "operationId": pay.operation_id, "paymentLinkId": str(pay.pk),
            "merchantId": merchant, "customerCode": "test-customer",
            "amount": amount if amount is not None else str(pay.amount_rubles),
            "status": status,
        }}

    def test_prices_are_owned_by_server_and_smart_requires_approval(self):
        offers = tariff_offers()
        self.assertEqual([(o["slug"], o["price_kopeks"]) for o in offers],
                         [("standard", 50000), ("smart", 99000)])
        with override_settings(NOSALE_SMART_MONTHLY_PRICE_RUB="0"):
            self.assertFalse(tariff_offers()[1]["can_buy"])
            with self.assertRaises(AcquiringUnavailable):
                create_checkout(self.user.pk, plan="smart")
        with self.assertRaises(AcquiringUnavailable):
            create_checkout(self.user.pk, plan="enterprise")

    def test_checkout_uses_server_plan_and_does_not_reuse_wrong_plan(self):
        standard = create_checkout(self.user.pk, plan="standard")
        self.assertEqual((standard.plan, standard.amount_kopeks), ("standard", 50000))
        self.assertEqual(create_checkout(self.user.pk, plan="standard").pk, standard.pk)
        smart = create_checkout(self.user.pk, plan="smart")
        self.assertEqual((smart.plan, smart.amount_kopeks), ("smart", 99000))
        self.assertNotEqual(smart.pk, standard.pk)
        self.assertEqual(create_checkout(self.user.pk, plan="smart").pk, smart.pk)
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())

    def test_price_change_cannot_rewrite_pending_invoice(self):
        standard = create_checkout(self.user.pk, plan="standard")
        with override_settings(NOSALE_MONTHLY_PRICE_RUB="600"):
            replacement = create_checkout(self.user.pk, plan="standard")
        self.assertNotEqual(replacement.pk, standard.pk)
        standard.refresh_from_db()
        self.assertEqual(standard.amount_kopeks, 50000)
        self.assertEqual(replacement.amount_kopeks, 60000)
        self.assertEqual(apply_verified_event(self.bank_event(standard)), "activated")
        self.assertEqual(apply_verified_event(self.bank_event(replacement)), "activated")
        self.assertEqual(AccessGrant.objects.filter(user=self.user, source="payment").count(), 2)

    def test_verified_smart_payment_activates_only_smart_without_trial(self):
        pay = create_checkout(self.user.pk, plan="smart")
        self.assertFalse(has_plan_capability(self.user.pk, "ai_advice"))
        self.assertEqual(apply_verified_event(self.bank_event(pay)), "activated")
        self.assertEqual(apply_verified_event(self.bank_event(pay)), "duplicate")
        grant = AccessGrant.objects.get(source_ref=f"nosale:tochka:{pay.pk}")
        self.assertEqual(grant.plan, "smart")
        self.assertEqual(effective_plan(self.user.pk), "smart")
        self.assertTrue(has_plan_capability(self.user.pk, "ai_advice"))
        self.assertEqual(AccessGrant.objects.filter(user=self.user).count(), 1)

    def test_smart_upgrade_is_scheduled_after_existing_standard_period(self):
        now = timezone.now()
        trial = AccessGrant.objects.create(
            user=self.user, source="trial", source_ref="trial:existing",
            plan="standard", starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(days=2),
        )
        pay = create_checkout(self.user.pk, plan="smart")
        self.assertEqual(apply_verified_event(self.bank_event(pay)), "activated")
        purchased = AccessGrant.objects.get(source_ref=f"nosale:tochka:{pay.pk}")
        self.assertEqual(purchased.plan, "smart")
        self.assertAlmostEqual((purchased.starts_at - trial.ends_at).total_seconds(), 0, delta=1)
        self.assertEqual(effective_plan(self.user.pk), "standard")
        self.assertFalse(has_plan_capability(self.user.pk, "ai_advice"))
        self.assertEqual(access_snapshot(self.user.pk)["upcoming_plan"], "smart")
        self.assertEqual(effective_plan(self.user.pk, at=trial.ends_at + timedelta(seconds=1)), "smart")

    def test_renewal_starts_after_existing_paid_access(self):
        first = create_checkout(self.user.pk, plan="standard")
        apply_verified_event(self.bank_event(first))
        previous = AccessGrant.objects.get(source_ref=f"nosale:tochka:{first.pk}")
        second = create_checkout(self.user.pk, plan="smart")
        apply_verified_event(self.bank_event(second))
        next_grant = AccessGrant.objects.get(source_ref=f"nosale:tochka:{second.pk}")
        self.assertAlmostEqual((next_grant.starts_at - previous.ends_at).total_seconds(), 0, delta=1)
        self.assertFalse(has_plan_capability(self.user.pk, "ai_advice"))
        self.assertEqual(effective_plan(self.user.pk, at=next_grant.starts_at + timedelta(seconds=1)), "smart")

    def test_verified_refund_revokes_only_matching_plan_grant(self):
        standard = create_checkout(self.user.pk, plan="standard")
        self.assertEqual(apply_verified_event(self.bank_event(standard)), "activated")
        smart = create_checkout(self.user.pk, plan="smart")
        self.assertEqual(apply_verified_event(self.bank_event(smart)), "activated")
        self.assertEqual(apply_verified_event(self.bank_event(smart, status="REFUNDED")), "refunded")
        self.assertEqual(apply_verified_event(self.bank_event(smart, status="REFUNDED")), "duplicate_refund")
        self.assertTrue(AccessGrant.objects.get(source_ref=f"nosale:tochka:{standard.pk}").revoked_at is None)
        self.assertIsNotNone(AccessGrant.objects.get(source_ref=f"nosale:tochka:{smart.pk}").revoked_at)
        self.assertEqual(effective_plan(self.user.pk), "standard")

    def test_bank_amount_and_merchant_mismatch_rejected(self):
        from .tochka import InvalidWebhook
        smart = create_checkout(self.user.pk, plan="smart")
        with self.assertRaises(InvalidWebhook):
            apply_verified_event(self.bank_event(smart, amount="500.00"))
        with self.assertRaises(InvalidWebhook):
            apply_verified_event(self.bank_event(smart, merchant="foreign"))
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())

    def test_checkout_ui_routes_plan_through_server_quote(self):
        page = self.client.get(reverse("subscription"))
        self.assertContains(page, "Standard")
        self.assertContains(page, "Smart")
        self.assertContains(page, "500,00", html=False)
        self.assertContains(page, "990,00", html=False)
        redirect = self.client.post(reverse("billing_checkout"), {
            "plan": "smart", "amount_kopeks": "1", "price": "0",
        })
        self.assertEqual(redirect.status_code, 302)
        self.assertEqual(SubscriptionPayment.objects.get(user=self.user).amount_kopeks, 99000)
        self.assertEqual(SubscriptionPayment.objects.get(user=self.user).plan, "smart")

    def test_unavailable_or_unknown_plan_does_not_create_checkout(self):
        self.client.post(reverse("billing_checkout"), {"plan": "something_else"})
        with override_settings(NOSALE_SMART_MONTHLY_PRICE_RUB="0"):
            self.client.post(reverse("billing_checkout"), {"plan": "smart"})
        self.assertFalse(SubscriptionPayment.objects.exists())
