"""Bank-facing request/response contract checks, without contacting acquiring."""
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .tochka import AcquiringUnavailable, create_tochka_link


class FakeResponse:
    status = 200

    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, limit):
        return self.body[:limit]


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request, self.timeout = request, timeout
        return self.response


@override_settings(
    TOCHKA_BILLING_ENABLED=True,
    TOCHKA_JWT_TOKEN="test-token-not-a-bank-secret",
    TOCHKA_CUSTOMER_CODE="300000092",
    TOCHKA_MERCHANT_ID="200000000001056",
    TOCHKA_API_BASE="https://enter.tochka.com/uapi",
    NOSALE_PAYMENT_RETURN_BASE="https://nosale.svdev.pro",
)
class TochkaTransportContractTests(SimpleTestCase):
    def setUp(self):
        self.payment = SimpleNamespace(pk=42, amount_kopeks=99000, period_months=1)

    def test_creates_real_documented_payment_link_request_and_parses_bank_data(self):
        opener = FakeOpener(FakeResponse({"Data": {
            "paymentLink": "https://merch.tochka.com/order/?uuid=test",
            "operationId": "example-bank-operation-id",
        }}))
        with patch("billing.tochka.build_opener", return_value=opener):
            payment_url, operation_id = create_tochka_link(self.payment)
        self.assertEqual(payment_url, "https://merch.tochka.com/order/?uuid=test")
        self.assertEqual(operation_id, "example-bank-operation-id")
        self.assertEqual(opener.request.full_url, "https://enter.tochka.com/uapi/acquiring/v1.0/payments")
        self.assertEqual(opener.request.get_method(), "POST")
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer test-token-not-a-bank-secret")
        self.assertEqual(opener.timeout, 12)
        body = json.loads(opener.request.data)
        self.assertEqual(list(body), ["Data"])
        data = body["Data"]
        self.assertEqual(data["amount"], 990.00)  # RUB, never kopeks
        self.assertEqual(data["customerCode"], "300000092")
        self.assertEqual(data["merchantId"], "200000000001056")
        self.assertEqual(data["paymentLinkId"], "42")
        self.assertEqual(data["paymentMode"], ["card", "sbp"])
        self.assertEqual(data["ttl"], 30)
        self.assertEqual(data["redirectUrl"], "https://nosale.svdev.pro/billing/return/?payment=42")
        self.assertEqual(data["failRedirectUrl"], data["redirectUrl"])
        self.assertEqual(data["webhookUrl"], "https://nosale.svdev.pro/billing/tochka/webhook/")

    def test_refuses_bank_response_without_operation_id(self):
        opener = FakeOpener(FakeResponse({"Data": {"paymentLink": "https://merch.tochka.com/order/test"}}))
        with patch("billing.tochka.build_opener", return_value=opener):
            with self.assertRaises(AcquiringUnavailable):
                create_tochka_link(self.payment)

    def test_refuses_bank_response_redirecting_to_foreign_domain(self):
        opener = FakeOpener(FakeResponse({"Data": {
            "paymentLink": "https://merch.tochka.com.attacker.invalid/pay",
            "operationId": "example-operation",
        }}))
        with patch("billing.tochka.build_opener", return_value=opener):
            with self.assertRaises(AcquiringUnavailable):
                create_tochka_link(self.payment)
