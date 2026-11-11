"""R5 controlled-beta acceptance across two isolated sellers (synthetic only).

No live WB, bank or email I/O. The existing Chromium suite separately checks
the actual email/PIN browser experience; these tests cover the shared state
transitions and seller/owner HTTP authorization on SQLite and PostgreSQL.
"""
import base64
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.access import effective_plan, guard_is_permitted, has_active_access, has_plan_capability
from billing.models import AccessGrant, OwnerTariffOffer, PaymentRefundAttempt, SubscriptionPayment
from billing.service import apply_verified_event
from guard.models import GuardAccount, Product
from guard.monitor import run_due_checks
from guard.stores import SESSION_KEY


def _synthetic_token(nm_id):
    body = base64.urlsafe_b64encode(json.dumps({
        "acc": 1, "s": (1 << 1) | (1 << 3), "synthetic_item": nm_id,
    }).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


class _SyntheticWB:
    """A separate isolated inventory per token; never makes HTTP requests."""

    def __init__(self, token):
        data = json.loads(base64.urlsafe_b64decode(
            token.split(".")[1] + "=" * (-len(token.split(".")[1]) % 4),
        ))
        self.nm_id = data["synthetic_item"]

    def validate_token(self, probe_prices=True):
        return None

    def get_product_cards(self):
        return [{"nmID": self.nm_id, "title": f"Isolated beta item {self.nm_id}", "photos": []}]

    def get_products(self):
        return [{
            "nmID": self.nm_id, "vendorCode": f"BETA-{self.nm_id}",
            "discount": 5,
            "sizes": [{"price": 1000, "discountedPrice": 950}],
        }]

    def submit_discounts(self, changes):
        raise AssertionError("Synthetic acceptance must never write to WB")

    def get_upload_state(self, upload_id):
        raise AssertionError("Synthetic acceptance must never poll real WB uploads")


@override_settings(
    SAAS_ACCESS_ENFORCED=True,
    SAAS_PIN_AUTH_ENABLED=True,
    SAAS_WB_TOKEN_VALIDATION_ENABLED=True,
    SAAS_WB_NETWORK_ENABLED=True,
    SAAS_WB_READ_ONLY=True,
    TOCHKA_BILLING_ENABLED=True,
    TOCHKA_MERCHANT_ID="test-nosale-merchant",
    TOCHKA_CUSTOMER_CODE="test-nosale-customer",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class R5TwoSellerAcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.seller_a = User.objects.create_user(
            "beta-a@example.test", "beta-a@example.test", "synthetic-only",
        )
        cls.seller_b = User.objects.create_user(
            "beta-b@example.test", "beta-b@example.test", "synthetic-only",
        )
        cls.owner = User.objects.create_superuser(
            "beta-owner@example.test", "beta-owner@example.test", "synthetic-only",
        )

    def setUp(self):
        self.a, self.b, self.admin = Client(), Client(), Client()
        self.a.force_login(self.seller_a)
        self.b.force_login(self.seller_b)
        self.admin.force_login(self.owner)
        for slug, amount in (("standard", 59900), ("smart", 99000)):
            OwnerTariffOffer.objects.create(
                slug=slug, name=slug.title(), price_kopeks=amount, enabled=False,
            )

    def _first_import(self, client, user, nm_id):
        self.assertFalse(has_active_access(user.pk))
        self.assertEqual(client.get(reverse("dashboard")).status_code, 200)
        with patch("guard.integrations.WBClient", _SyntheticWB), patch(
            "guard.monitor.WBClient", _SyntheticWB
        ):
            response = client.post(reverse("connect_wb"), {"token": _synthetic_token(nm_id)})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(AccessGrant.objects.filter(user=user).count(), 0)
            self.assertEqual(run_due_checks(), 1)
        account = GuardAccount.objects.get(user=user)
        self.assertIsNotNone(account.initial_sync_completed_at)
        self.assertTrue(Product.objects.filter(account=account, nm_id=nm_id, active=True).exists())
        self.assertTrue(has_active_access(user.pk))
        self.assertEqual(
            AccessGrant.objects.filter(user=user, source="trial", plan="standard").count(), 1,
        )
        self.assertFalse(account.enabled)  # Read-only fixture may not send WB prices.
        return account

    def _event(self, payment, status, *, merchant="test-nosale-merchant"):
        return {"data": {
            "webhookType": "acquiringInternetPayment",
            "operationId": payment.operation_id,
            "paymentLinkId": f"nosale-{payment.pk}",
            "merchantId": merchant,
            "customerCode": "test-nosale-customer",
            "amount": str(payment.amount_rubles),
            "status": status,
        }}

    def test_two_sellers_through_wb_trial_paywall_bank_and_refund(self):
        account_a = self._first_import(self.a, self.seller_a, 920101)
        account_b = self._first_import(self.b, self.seller_b, 920202)
        self.assertEqual(AccessGrant.objects.filter(source="trial").count(), 2)
        self.assertContains(self.a.get(reverse("dashboard")), "Isolated beta item 920101")
        self.assertNotContains(self.a.get(reverse("dashboard")), "Isolated beta item 920202")
        self.assertContains(self.b.get(reverse("dashboard")), "Isolated beta item 920202")
        self.assertNotContains(self.b.get(reverse("dashboard")), "Isolated beta item 920101")
        self.assertEqual(self.b.post(
            reverse("select_store", args=[account_a.pk]),
        ).status_code, 404)
        self.assertEqual(self.b.post(
            reverse("product_rule", args=[920101]), {"allowed_discount": "9"},
        ).status_code, 404)
        forged = self.b.session
        forged[SESSION_KEY] = account_a.pk
        forged.save()
        self.assertContains(self.b.get(reverse("dashboard")), "Isolated beta item 920202")
        self.assertNotContains(self.b.get(reverse("dashboard")), "Isolated beta item 920101")
        self.assertEqual(GuardAccount.objects.get(pk=account_b.pk).user_id, self.seller_b.pk)

        # Trial belongs to the seller, cannot be prolonged by a payment URL.
        trial_a = AccessGrant.objects.get(user=self.seller_a, source="trial")
        trial_a.ends_at = timezone.now() - timedelta(seconds=1)
        trial_a.save(update_fields=["ends_at"])
        self.assertFalse(has_active_access(self.seller_a.pk))
        self.assertFalse(guard_is_permitted(account_a))
        self.assertTrue(has_active_access(self.seller_b.pk))
        self.assertFalse(has_plan_capability(self.seller_a.pk, "ai_advice"))
        self.assertEqual(self.a.get(reverse("subscription")).status_code, 200)
        no_checkout = self.a.post(reverse("billing_checkout"), {"plan": "smart"})
        self.assertEqual(no_checkout.status_code, 302)
        self.assertFalse(SubscriptionPayment.objects.exists())

        # Only the isolated test DB enables offers. The server, not POST fields,
        # fixes quote amount. The browser return never grants paid access.
        OwnerTariffOffer.objects.filter(slug__in=("standard", "smart")).update(enabled=True)
        with patch("billing.service.create_tochka_link",
                   side_effect=lambda pay: (
                       "https://merch.tochka.com/order/synthetic-only", f"beta-operation-{pay.pk}"
                   )) as provider:
            redirect_response = self.a.post(reverse("billing_checkout"), {
                "plan": "smart", "amount_kopeks": "1",
            })
        self.assertEqual(redirect_response.status_code, 302)
        self.assertEqual(redirect_response["Location"],
                         "https://merch.tochka.com/order/synthetic-only")
        provider.assert_called_once()
        payment = SubscriptionPayment.objects.get(user=self.seller_a)
        self.assertEqual((payment.amount_kopeks, payment.plan, payment.status),
                         (99000, "smart", "pending"))
        self.assertEqual(self.b.get(
            reverse("billing_return") + f"?payment={payment.pk}",
        ).status_code, 302)
        self.assertEqual(self.a.get(
            reverse("billing_return") + f"?payment={payment.pk}",
        ).status_code, 200)
        self.assertFalse(has_active_access(self.seller_a.pk))

        from billing.tochka import InvalidWebhook
        with self.assertRaises(InvalidWebhook):
            apply_verified_event(self._event(payment, "APPROVED", merchant="foreign-merchant"))
        self.assertEqual(apply_verified_event(self._event(payment, "APPROVED")), "activated")
        self.assertEqual(apply_verified_event(self._event(payment, "APPROVED")), "duplicate")
        self.assertEqual(effective_plan(self.seller_a.pk), "smart")
        self.assertTrue(has_plan_capability(self.seller_a.pk, "ai_advice"))
        self.assertEqual(effective_plan(self.seller_b.pk), "standard")
        self.assertFalse(has_plan_capability(self.seller_b.pk, "ai_advice"))

        # A verified bank refund removes only seller A's matching grant.
        self.assertEqual(apply_verified_event(self._event(payment, "REFUNDED")), "refunded")
        self.assertEqual(apply_verified_event(self._event(payment, "APPROVED")), "ignored")
        self.assertFalse(has_active_access(self.seller_a.pk))
        self.assertTrue(has_active_access(self.seller_b.pk))
        self.assertEqual(
            AccessGrant.objects.filter(user=self.seller_a, source="payment",
                                       revoked_at__isnull=False).count(), 1,
        )
        self.assertEqual(AccessGrant.objects.filter(user=self.seller_b, source="payment").count(), 0)
        self.assertEqual(AccessGrant.objects.filter(source="trial").count(), 2)

    @override_settings(SAAS_PIN_AUTH_ENABLED=False)
    def test_owner_refund_ui_confirms_once_and_never_exposes_bank_post_to_seller(self):
        payment = SubscriptionPayment.objects.create(
            user=self.seller_a, provider="tochka", plan="standard",
            operation_id="beta-refund-1", amount_kopeks=59900, period_months=1,
            status="pending",
        )
        self.assertEqual(apply_verified_event(self._event(payment, "APPROVED")), "activated")
        self.assertTrue(has_active_access(self.seller_a.pk))
        url = reverse("admin:billing_subscriptionpayment_refund", args=[payment.pk])
        self.assertNotEqual(self.b.get(url).status_code, 200)
        self.assertNotEqual(self.b.post(url, {"confirm_refund": "on"}).status_code, 200)
        bank_approved = {
            "operationId": payment.operation_id,
            "paymentLinkId": f"nosale-{payment.pk}",
            "merchantId": "test-nosale-merchant",
            "customerCode": "test-nosale-customer",
            "amount": "599.00",
            "status": "APPROVED",
            "Order": [{"type": "approval", "amount": 599.0}],
        }
        bank_refunded = {**bank_approved, "status": "REFUNDED",
                         "Order": [{"type": "refund", "amount": 599.0}]}
        self.assertEqual(self.admin.get(url).status_code, 200)
        with patch("billing.refunds.get_tochka_operation", return_value=bank_approved), \
             patch("billing.refunds._send_full_refund", return_value={
                 "isRefund": True, "operationId": payment.operation_id,
                 "amount": "599.00", "orderId": "beta-child-refund-1",
             }) as send, \
             patch("billing.service.get_tochka_operation", return_value=bank_refunded):
            self.assertEqual(self.admin.post(url, {"confirm_refund": "on"}).status_code, 302)
            self.assertEqual(self.admin.post(url, {"confirm_refund": "on"}).status_code, 302)
            send.assert_called_once()
        payment.refresh_from_db()
        self.assertEqual(payment.status, "refunded")
        self.assertFalse(has_active_access(self.seller_a.pk))
        self.assertTrue(PaymentRefundAttempt.objects.filter(
            payment=payment, requested_by=self.owner, status="confirmed",
        ).exists())
        self.assertFalse(has_active_access(self.seller_b.pk))
