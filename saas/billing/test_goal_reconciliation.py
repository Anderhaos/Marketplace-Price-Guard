"""Unauthenticated browser events are never used for acquiring reconciliation."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import AccessGrant, SubscriptionPayment
from .service import reconcile_payment
from .tochka import InvalidWebhook

@override_settings(
    TOCHKA_BILLING_ENABLED=True,
    TOCHKA_CUSTOMER_CODE="300000092",
    TOCHKA_MERCHANT_ID="200000000001056",
)
class GoalReconciliationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="reconcile@example.test")
        self.payment = SubscriptionPayment.objects.create(
            user=self.user, amount_kopeks=99000,
            operation_id="op-test-42", status="pending", period_months=1,
        )
        self.operation = dict(
            operationId="op-test-42", paymentLinkId=str(self.payment.pk),
            merchantId="200000000001056", customerCode="300000092",
            amount="990.00", status="APPROVED",
        )

    def test_authenticated_bank_read_recovers_missing_webhook(self):
        with patch("billing.service.get_tochka_operation", return_value=self.operation):
            self.assertEqual(reconcile_payment(self.payment), "activated")
            self.assertEqual(reconcile_payment(self.payment), "duplicate")
        self.assertEqual(AccessGrant.objects.filter(user=self.user, source="payment").count(), 1)

    def test_bank_read_refund_revokes_just_matching_grant(self):
        with patch("billing.service.get_tochka_operation", return_value=self.operation):
            reconcile_payment(self.payment)
        refunded = {**self.operation, "status": "REFUNDED"}
        with patch("billing.service.get_tochka_operation", return_value=refunded):
            self.assertEqual(reconcile_payment(self.payment), "refunded")
        self.assertIsNotNone(AccessGrant.objects.get(user=self.user, source="payment").revoked_at)

    def test_wrong_amount_or_merchant_never_grants(self):
        for change in ({"amount": "999.00"}, {"merchantId": "foreign"}, {"paymentLinkId": "99999"}):
            with self.subTest(change=change), patch(
                "billing.service.get_tochka_operation", return_value={**self.operation, **change}
            ):
                with self.assertRaises(InvalidWebhook):
                    reconcile_payment(self.payment)
        self.assertFalse(AccessGrant.objects.filter(user=self.user, source="payment").exists())

    def test_partial_refund_requires_review_not_hidden_payment_change(self):
        operation = {**self.operation, "status": "REFUNDED_PARTIALLY"}
        with patch("billing.service.get_tochka_operation", return_value=operation):
            self.assertEqual(reconcile_payment(self.payment), "partial_refund_manual_review")
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "pending")
        self.assertFalse(AccessGrant.objects.exists())

@override_settings(
    TOCHKA_BILLING_ENABLED=True,
    TOCHKA_JWT_TOKEN="test-only-token",
    TOCHKA_API_BASE="https://enter.tochka.com/uapi",
)
class GoalBankTransportTests(TestCase):
    def test_get_operation_uses_authenticated_bank_schema(self):
        import json
        from unittest.mock import patch
        from .tochka import get_tochka_operation
        operation = {"operationId": "op-123", "status": "APPROVED"}
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, count): return json.dumps({"Data": operation}).encode()[:count]
        class Opener:
            def open(self, request, timeout):
                self.request, self.timeout = request, timeout
                return Response()
        opener = Opener()
        with patch("billing.tochka.build_opener", return_value=opener):
            self.assertEqual(get_tochka_operation("op-123"), operation)
        self.assertEqual(opener.request.full_url, "https://enter.tochka.com/uapi/acquiring/v1.0/payments/op-123")
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer test-only-token")
        self.assertEqual(opener.timeout, 12)

    def test_invalid_bank_operation_identifier_is_rejected_before_network(self):
        from .tochka import get_tochka_operation, AcquiringUnavailable
        with self.assertRaises(AcquiringUnavailable):
            get_tochka_operation("../../../users")

    def test_legacy_bank_operation_array_still_supported(self):
        import json
        from .tochka import get_tochka_operation
        operation = {"operationId": "old-123", "status": "APPROVED"}
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, count):
                return json.dumps({"Data": {"Operation": [operation]}}).encode()[:count]
        class Opener:
            def open(self, *_args, **_kwargs): return Response()
        with patch("billing.tochka.build_opener", return_value=Opener()):
            self.assertEqual(get_tochka_operation("old-123"), operation)

    def test_bank_readback_rejects_mismatched_operation_or_ambiguous_response(self):
        import json
        from .tochka import AcquiringUnavailable, get_tochka_operation
        cases = [
            {"Data": {"operationId": "foreign", "status": "APPROVED"}},
            {"Data": {"Operation": [
                {"operationId": "op-123"}, {"operationId": "op-123"},
            ]}},
            {"Data": []},
        ]
        for payload in cases:
            class Response:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *_): return False
                def read(self, count): return json.dumps(payload).encode()[:count]
            class Opener:
                def open(self, *_args, **_kwargs): return Response()
            with self.subTest(payload=payload), patch("billing.tochka.build_opener", return_value=Opener()):
                with self.assertRaises(AcquiringUnavailable):
                    get_tochka_operation("op-123")
