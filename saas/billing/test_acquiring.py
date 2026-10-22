"""Tochka acquiring integration contract, matching MiniBook's signed-webhook model."""
import base64
import json
import time
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AccessGrant, SubscriptionPayment
from .service import apply_verified_event, create_checkout
from .tochka import (
    AcquiringUnavailable, InvalidWebhook, normalize_bank_event,
    verify_tochka_webhook,
)


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


@override_settings(
    TOCHKA_BILLING_ENABLED=True,
    TOCHKA_JWT_TOKEN="test-only-do-not-use",
    TOCHKA_CUSTOMER_CODE="test-customer",
    TOCHKA_MERCHANT_ID="test-merchant",
    TOCHKA_API_BASE="https://enter.tochka.com/uapi",
    NOSALE_MONTHLY_PRICE_RUB="990.00",
    NOSALE_PAYMENT_RETURN_BASE="https://nosale.svdev.pro",
    SAAS_ACCESS_ENFORCED=True,
)
class AcquiringTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            "billing@example.test", "billing@example.test", "1234"
        )
        cls.other = User.objects.create_user(
            "another@example.test", "another@example.test", "1234"
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self.key.public_key().public_numbers()
        self.override = override_settings(TOCHKA_WEBHOOK_JWK_JSON=json.dumps({
            "kty": "RSA", "n": b64(numbers.n.to_bytes(256, "big")),
            "e": b64(numbers.e.to_bytes(3, "big")),
        }))
        self.override.enable()
        self.addCleanup(self.override.disable)

    def payment(self):
        return SubscriptionPayment.objects.create(
            user=self.user, provider="tochka", amount_kopeks=99000,
            operation_id="op-test-123", period_months=1,
        )

    def event(self, payment, *, status="APPROVED", amount="990.00", merchant="test-merchant", operation="op-test-123"):
        return {"iat": int(time.time()), "exp": int(time.time()) + 3600,
                "data": {
                    "webhookType": "acquiringInternetPayment",
                    "operationId": operation,
                    "paymentLinkId": str(payment.pk), "status": status,
                    "amount": amount, "merchantId": merchant,
                    "customerCode": "test-customer",
                }}

    def signed(self, event, *, tamper=False):
        header = b64(b'{"typ":"JWT","alg":"RS256"}')
        body = b64(json.dumps(event).encode())
        original = (header + "." + body).encode()
        sig = self.key.sign(original, padding.PKCS1v15(), hashes.SHA256())
        encoded = header + "." + body + "." + b64(sig)
        if tamper:
            encoded = encoded[:-4] + "AAAA"
        return encoded

    def post_signed(self, payload, *, tamper=False):
        return self.client.post(
            reverse("tochka_webhook"), data=self.signed(payload, tamper=tamper),
            content_type="text/plain",
        )

    @patch("billing.service.create_tochka_link", return_value=("https://merch.tochka.com/order/test", "op-test-123"))
    def test_checkout_creates_once_and_reuses_recent_pending_link(self, api):
        payment = create_checkout(self.user.pk)
        self.assertEqual(payment.status, "pending")
        self.assertEqual(payment.amount_kopeks, 99000)
        self.assertEqual(payment.payment_url, "https://merch.tochka.com/order/test")
        self.assertEqual(create_checkout(self.user.pk).pk, payment.pk)
        api.assert_called_once()
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())

    @patch("billing.service.create_tochka_link", side_effect=AcquiringUnavailable("offline"))
    def test_failed_bank_request_does_not_activate_and_is_recorded(self, api):
        with self.assertRaises(AcquiringUnavailable):
            create_checkout(self.user.pk)
        self.assertEqual(SubscriptionPayment.objects.get(user=self.user).status, "failed")
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())

    def test_trial_does_not_get_extended_by_browser_return(self):
        grant = AccessGrant.objects.create(
            user=self.user, source="trial", source_ref="test-trial",
            starts_at=timezone.now(), ends_at=timezone.now() + timedelta(days=2),
        )
        payment = self.payment()
        self.assertEqual(
            self.client.get(reverse("billing_return") + f"?payment={payment.pk}").status_code, 200,
        )
        grant.refresh_from_db()
        self.assertEqual(AccessGrant.objects.filter(user=self.user).count(), 1)
        self.assertEqual(payment.status, "pending")

    def test_correct_signed_approval_activates_once_and_extends_after_trial(self):
        now = timezone.now()
        trial = AccessGrant.objects.create(
            user=self.user, source="trial", source_ref="test-trial",
            starts_at=now, ends_at=now + timedelta(days=2),
        )
        payment = self.payment()
        self.assertEqual(self.post_signed(self.event(payment)).status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "paid")
        new_grant = AccessGrant.objects.get(user=self.user, source="payment")
        self.assertAlmostEqual((new_grant.starts_at-trial.ends_at).total_seconds(), 0, delta=1)
        self.assertEqual(AccessGrant.objects.filter(user=self.user).count(), 2)
        self.post_signed(self.event(payment))
        self.assertEqual(AccessGrant.objects.filter(user=self.user).count(), 2)

    def test_unverified_signature_never_activates(self):
        payment = self.payment()
        self.assertEqual(self.post_signed(self.event(payment), tamper=True).status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "pending")
        self.assertFalse(AccessGrant.objects.exists())

    def test_missing_or_wrong_amount_merchant_or_operation_never_activates(self):
        payment = self.payment()
        for kwargs in ({"amount": "900.00"}, {"merchant": "someone-else"},
                       {"operation": "foreign-operation"}):
            with self.subTest(kwargs=kwargs):
                self.post_signed(self.event(payment, **kwargs))
                self.assertFalse(AccessGrant.objects.exists())
        self.assertEqual(SubscriptionPayment.objects.get(pk=payment.pk).status, "pending")

    def test_authorized_or_created_is_not_paid(self):
        payment = self.payment()
        for status in ("CREATED", "AUTHORIZED", "WAIT_FULL_PAYMENT"):
            self.post_signed(self.event(payment, status=status))
        self.assertFalse(AccessGrant.objects.exists())

    def test_refund_revokes_only_matching_paid_grant(self):
        payment = self.payment()
        self.post_signed(self.event(payment))
        self.post_signed(self.event(payment, status="REFUNDED"))
        payment.refresh_from_db()
        self.assertEqual(payment.status, "refunded")
        self.assertIsNotNone(AccessGrant.objects.get(source_ref=f"nosale:tochka:{payment.pk}").revoked_at)

    def test_other_seller_cannot_see_payment_return(self):
        payment = self.payment()
        self.client.force_login(self.other)
        result = self.client.get(reverse("billing_return") + f"?payment={payment.pk}")
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result["Location"], reverse("subscription"))

    def test_without_configuration_checkout_is_disabled(self):
        with override_settings(TOCHKA_BILLING_ENABLED=False):
            html = self.client.get(reverse("subscription"))
            self.assertContains(html, "Оплата пока не подключена")
            self.assertNotContains(html, "Перейти к оплате")
            response = self.client.post(reverse("billing_checkout"))
            self.assertRedirects(response, reverse("subscription"))
            self.assertFalse(SubscriptionPayment.objects.exists())

    def test_bank_webhook_not_json_or_unsigned(self):
        payment = self.payment()
        result = self.client.post(reverse("tochka_webhook"), data='{"status":"APPROVED"}',
                                  content_type="application/json")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(payment.status, "pending")
        self.assertFalse(AccessGrant.objects.exists())

    def test_unauthenticated_checkout_redirects_without_payment(self):
        self.client.logout()
        self.assertEqual(self.client.post(reverse("billing_checkout")).status_code, 302)
        self.assertFalse(SubscriptionPayment.objects.exists())
