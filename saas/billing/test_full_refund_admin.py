"""NoSale owner-only full refund UI and real Tochka contract (all bank calls mocked)."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AccessGrant, PaymentRefundAttempt, SubscriptionPayment
from .refunds import RefundUnavailable, start_full_refund


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    SECURE_SSL_REDIRECT=False,
    SAAS_PIN_AUTH_ENABLED=False,
    TOCHKA_BILLING_ENABLED=True,
    TOCHKA_API_BASE="https://enter.tochka.com/uapi",
    TOCHKA_CUSTOMER_CODE="300000092",
    TOCHKA_MERCHANT_ID="200000000001056",
    TOCHKA_JWT_TOKEN="fake-only-for-tests",
)
class OwnerFullRefundTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("owner-test@example.test", "owner-test@example.test", "pass")
        self.staff = User.objects.create_user("staff-test@example.test", "staff-test@example.test", "pass", is_staff=True)
        self.customer = User.objects.create_user("customer-test@example.test", "customer-test@example.test", "pass")
        self.payment = SubscriptionPayment.objects.create(
            user=self.customer, provider="tochka", plan="standard",
            operation_id="operation-42", amount_kopeks=59900, period_months=1,
            status="paid", paid_at=timezone.now(),
        )
        self.grant = AccessGrant.objects.create(
            user=self.customer, source="payment", plan="standard",
            source_ref=f"nosale:tochka:{self.payment.pk}",
            starts_at=timezone.now(), ends_at=timezone.now() + timezone.timedelta(days=30),
        )
        self.url = reverse("admin:billing_subscriptionpayment_refund", args=[self.payment.pk])
        self.changelist = reverse("admin:billing_subscriptionpayment_changelist")
        self.bank = {
            "operationId": self.payment.operation_id,
            "paymentLinkId": f"nosale-{self.payment.pk}",
            "merchantId": "200000000001056",
            "customerCode": "300000092",
            "amount": "599.00",
            "status": "APPROVED",
            "Order": [{"type": "approval", "amount": 599.0}],
        }

    def test_owner_sees_confirmation_and_no_get_side_effect(self):
        self.client.force_login(self.owner)
        with patch("billing.refunds._send_full_refund") as bank_send:
            resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "599.00 ₽")
        self.assertContains(resp, "name=\"confirm_refund\"")
        bank_send.assert_not_called()
        page = self.client.get(self.changelist)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self.url)
        self.assertContains(page, "Вернуть 599.00 ₽")
        audit = self.client.get(reverse("admin:billing_paymentrefundattempt_changelist"))
        self.assertEqual(audit.status_code, 200)

    def test_seller_and_staff_cannot_refund(self):
        for user in (self.customer, self.staff):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                with patch("billing.refunds._send_full_refund") as bank_send:
                    self.assertNotEqual(self.client.get(self.url).status_code, 200)
                    self.assertNotEqual(self.client.post(self.url, {"confirm_refund": "on"}).status_code, 200)
                    bank_send.assert_not_called()
        self.assertFalse(PaymentRefundAttempt.objects.exists())

    def test_post_requires_explicit_confirmation(self):
        self.client.force_login(self.owner)
        with patch("billing.refunds._send_full_refund") as bank_send:
            resp = self.client.post(self.url, {})
        self.assertEqual(resp.status_code, 200)
        bank_send.assert_not_called()
        self.assertFalse(PaymentRefundAttempt.objects.exists())

    def test_csrf_protection_is_active(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        resp = client.post(self.url, {"confirm_refund": "on"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(PaymentRefundAttempt.objects.exists())

    @patch("billing.refunds.reconcile_payment", return_value="ignored")
    @patch("billing.refunds._send_full_refund")
    @patch("billing.refunds.get_tochka_operation")
    def test_owner_posts_one_full_refund_audited_and_duplicate_is_blocked(self, bank_get, bank_send, reconciler):
        bank_get.return_value = self.bank
        bank_send.return_value = {
            "isRefund": True, "operationId": "operation-42",
            "amount": "599.00", "date": "2026-11-09", "orderId": "refund-42",
        }
        self.client.force_login(self.owner)
        self.assertRedirects(
            self.client.post(self.url, {"confirm_refund": "on"}),
            self.changelist,
        )
        attempt = PaymentRefundAttempt.objects.get(payment=self.payment)
        self.assertEqual(attempt.status, "submitted")
        self.assertEqual(attempt.amount_kopeks, 59900)
        self.assertEqual(attempt.bank_refund_order_id, "refund-42")
        self.assertEqual(attempt.requested_by, self.owner)
        self.assertEqual(self.payment.status, "paid")
        self.grant.refresh_from_db()
        self.assertIsNone(self.grant.revoked_at)
        bank_send.assert_called_once_with(self.payment)
        bank_get.assert_called_once_with("operation-42")
        reconciler.assert_called_once()
        self.assertRedirects(
            self.client.post(self.url, {"confirm_refund": "on"}),
            self.changelist,
        )
        bank_send.assert_called_once()
        self.assertEqual(PaymentRefundAttempt.objects.count(), 1)

    @patch("billing.refunds._send_full_refund")
    @patch("billing.refunds.get_tochka_operation")
    def test_bank_preflight_blocks_wrong_identity_amount_and_partial_refund(self, bank_get, bank_send):
        invalid = [
            {**self.bank, "amount": "100.00"},
            {**self.bank, "merchantId": "wrong"},
            {**self.bank, "paymentLinkId": "beclients-1"},
            {**self.bank, "status": "REFUNDED"},
            {**self.bank, "Order": [{"type": "refund"}]},
        ]
        for sample in invalid:
            with self.subTest(sample=sample):
                bank_get.return_value = sample
                with self.assertRaises(RefundUnavailable):
                    start_full_refund(self.payment.pk, self.owner)
        bank_send.assert_not_called()
        self.assertFalse(PaymentRefundAttempt.objects.exists())

    @patch("billing.refunds._send_full_refund", side_effect=RefundUnavailable("Timeout"))
    @patch("billing.refunds.get_tochka_operation")
    def test_timeout_stays_uncertain_and_never_retries_bank(self, bank_get, bank_send):
        bank_get.return_value = self.bank
        outcome = start_full_refund(self.payment.pk, self.owner)
        self.assertEqual(outcome.status, "uncertain")
        with self.assertRaises(RefundUnavailable):
            start_full_refund(self.payment.pk, self.owner)
        bank_send.assert_called_once()
        self.grant.refresh_from_db()
        self.assertIsNone(self.grant.revoked_at)

    @patch("billing.refunds.reconcile_payment")
    @patch("billing.refunds._send_full_refund")
    @patch("billing.refunds.get_tochka_operation")
    def test_bank_confirmed_refund_revokes_grant(self, bank_get, bank_send, reconciler):
        bank_get.return_value = self.bank
        bank_send.return_value = {
            "isRefund": True, "operationId": "operation-42",
            "amount": 599.0, "date": "2026-11-09", "orderId": "refund-42",
        }
        def apply_bank_verified_refund(payment):
            from .service import apply_verified_event
            return apply_verified_event({"data": {
                "webhookType": "acquiringInternetPayment",
                "operationId": payment.operation_id,
                "paymentLinkId": f"nosale-{payment.pk}",
                "merchantId": "200000000001056",
                "customerCode": "300000092",
                "amount": "599.00",
                "status": "REFUNDED",
            }})
        reconciler.side_effect = apply_bank_verified_refund
        result = start_full_refund(self.payment.pk, self.owner)
        self.assertEqual(result.status, "confirmed")
        self.payment.refresh_from_db()
        self.grant.refresh_from_db()
        self.assertEqual(self.payment.status, "refunded")
        self.assertIsNotNone(self.grant.revoked_at)

    def test_ledger_is_readonly_in_admin(self):
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("admin:billing_subscriptionpayment_change", args=[self.payment.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'name="amount_kopeks"')
        self.assertNotContains(resp, 'name="status"')
        self.assertNotContains(resp, 'name="_save"')

    def test_refund_transport_uses_exact_documented_amount_and_real_path(self):
        from .refunds import _send_full_refund
        class Reply:
            status = 200
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,_):return b'{"Data":{"isRefund":true,"operationId":"operation-42","amount":599,"orderId":"123","date":"2026-11-09"}}'
        class Opener:
            def __init__(self):self.request=None
            def open(self,request,timeout):
                self.request=request
                self.timeout=timeout
                return Reply()
        opener=Opener()
        with patch("billing.refunds.build_opener",return_value=opener):
            answer=_send_full_refund(self.payment)
        self.assertTrue(answer["isRefund"])
        self.assertEqual(opener.request.get_method(),"POST")
        self.assertEqual(opener.request.full_url,"https://enter.tochka.com/uapi/acquiring/v1.0/payments/operation-42/refund")
        self.assertEqual(opener.request.get_header("Authorization"),"Bearer fake-only-for-tests")
        self.assertEqual(opener.timeout,12)
        self.assertEqual(__import__("json").loads(opener.request.data),{"Data":{"amount":599.0}})

    @patch("billing.refunds._send_full_refund")
    @patch("billing.refunds.get_tochka_operation")
    def test_malformed_success_reply_is_not_retried_or_accepted(self, bank_get, bank_send):
        bank_get.return_value = self.bank
        bank_send.return_value = {
            "isRefund": True, "operationId": "operation-42",
            "amount": "not-money", "orderId": "refund-42",
        }
        result = start_full_refund(self.payment.pk, self.owner)
        self.assertEqual(result.status, "uncertain")
        self.assertEqual(result.failure_code, "bank_reply_mismatch")
        bank_send.assert_called_once()
        with self.assertRaises(RefundUnavailable):
            start_full_refund(self.payment.pk, self.owner)
        bank_send.assert_called_once()
        self.grant.refresh_from_db()
        self.assertIsNone(self.grant.revoked_at)

    def test_periodic_bank_refund_confirmation_updates_audit_and_access(self):
        from .service import reconcile_payment
        attempt = PaymentRefundAttempt.objects.create(
            payment=self.payment, requested_by=self.owner,
            bank_operation_id=self.payment.operation_id,
            amount_kopeks=self.payment.amount_kopeks, status="submitted",
        )
        returned = {**self.bank, "status": "REFUNDED"}
        with patch("billing.service.get_tochka_operation", return_value=returned):
            outcome = reconcile_payment(self.payment)
        self.assertEqual(outcome, "refunded")
        attempt.refresh_from_db()
        self.payment.refresh_from_db()
        self.grant.refresh_from_db()
        self.assertEqual(attempt.status, "confirmed")
        self.assertEqual(attempt.bank_status, "REFUNDED")
        self.assertEqual(self.payment.status, "refunded")
        self.assertIsNotNone(self.grant.revoked_at)

    def test_partial_refund_is_audited_as_manual_review_not_confirmed(self):
        from .service import reconcile_payment
        attempt = PaymentRefundAttempt.objects.create(
            payment=self.payment, requested_by=self.owner,
            bank_operation_id=self.payment.operation_id,
            amount_kopeks=self.payment.amount_kopeks, status="submitted",
        )
        partially_returned = {**self.bank, "status": "REFUNDED_PARTIALLY"}
        with patch("billing.service.get_tochka_operation", return_value=partially_returned):
            outcome = reconcile_payment(self.payment)
        self.assertEqual(outcome, "partial_refund_manual_review")
        attempt.refresh_from_db()
        self.payment.refresh_from_db()
        self.grant.refresh_from_db()
        self.assertEqual(attempt.status, "uncertain")
        self.assertEqual(attempt.bank_status, "REFUNDED_PARTIALLY")
        self.assertEqual(self.payment.status, "paid")
        self.assertIsNone(self.grant.revoked_at)
