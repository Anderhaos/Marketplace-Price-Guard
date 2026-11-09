"""Contract checks for NoSale's official-bank provisioning preflight."""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from billing.management.commands.tochka_preflight import (
    EVENT_NAME, WEBHOOK_URL, retailer_for_nosale, webhook_matches,
)

BANK_RETAILERS = {"Data": {"Retailer": [{
    "merchantId": "200000000001056",
    "url": "https://nosale.pro",
    "status": "REG",
    "isActive": True,
    "paymentModes": ["sbp", "card"],
    "cashbox": "tochka",
}]}}

CONFIG = {
    "TOCHKA_BILLING_ENABLED": False,
    "NOSALE_FISCALIZATION_CONFIRMED": False,
    "TOCHKA_JWT_TOKEN": "fake-for-patched-tests",
    "TOCHKA_CUSTOMER_CODE": "300000092",
    "TOCHKA_MERCHANT_ID": "200000000001056",
    "TOCHKA_WEBHOOK_JWK_JSON": '{"kty":"RSA","e":"AQAB","n":"unit-test-modulus"}',
    "TOCHKA_API_BASE": "https://enter.tochka.com/uapi",
}


class TochkaPreflightTest(SimpleTestCase):
    def test_retailer_requires_correct_site_status_and_active(self):
        self.assertIsNotNone(retailer_for_nosale(BANK_RETAILERS, "200000000001056"))
        for key, value in (
            ("status", "NEW"), ("isActive", False),
            ("url", "https://beclients.ru"), ("merchantId", "200000000001999"),
        ):
            data = {"Data": {"Retailer": [{**BANK_RETAILERS["Data"]["Retailer"][0], key: value}]}}
            self.assertIsNone(retailer_for_nosale(data, "200000000001056"))

    def test_webhook_must_match_exact_origin_and_event(self):
        self.assertTrue(webhook_matches({"Data": {
            "url": WEBHOOK_URL, "webhooksList": [EVENT_NAME]
        }}))
        self.assertFalse(webhook_matches({"Data": {
            "url": "https://beclients.ru/billing/tochka/webhook/",
            "webhooksList": [EVENT_NAME]
        }}))
        self.assertFalse(webhook_matches({"Data": {
            "url": WEBHOOK_URL, "webhooksList": ["incomingPayment"]
        }}))

    @override_settings(**CONFIG)
    @patch.dict("os.environ", {"NOSALE_TOCHKA_CLIENT_ID": "NoSaleDedicated123"})
    @patch("billing.management.commands.tochka_preflight._request")
    def test_register_once_only_when_absent(self, fetch):
        fetch.side_effect = [
            BANK_RETAILERS,
            {"kty": "RSA", "e": "AQAB", "n": "unit-test-modulus"},
            None,
            {"Data": {"url": WEBHOOK_URL, "webhooksList": [EVENT_NAME]}},
        ]
        output = StringIO()
        call_command("tochka_preflight", online=True, register_webhook=True, stdout=output)
        self.assertIn("registered", output.getvalue())
        self.assertEqual(fetch.call_count, 4)
        path, args, kwargs = fetch.mock_calls[-1][1][0], fetch.mock_calls[-1][1][1:], fetch.mock_calls[-1][2]
        self.assertEqual(path, "/webhook/v1.0/NoSaleDedicated123")
        self.assertEqual(kwargs["method"], "PUT")
        self.assertEqual(kwargs["body"], {"webhooksList": [EVENT_NAME], "url": WEBHOOK_URL})

    @override_settings(**CONFIG)
    @patch.dict("os.environ", {"NOSALE_TOCHKA_CLIENT_ID": "NoSaleDedicated123"})
    @patch("billing.management.commands.tochka_preflight._request")
    def test_never_overwrite_existing_foreign_webhook(self, fetch):
        fetch.side_effect = [
            BANK_RETAILERS,
            {"kty": "RSA", "e": "AQAB", "n": "unit-test-modulus"},
            {"Data": {"url": "https://other.example/webhook", "webhooksList": [EVENT_NAME]}},
        ]
        with self.assertRaisesMessage(CommandError, "not overwriting"):
            call_command("tochka_preflight", online=True, register_webhook=True, stdout=StringIO())
        self.assertEqual(fetch.call_count, 3)

    @patch("billing.management.commands.tochka_preflight._request")
    def test_local_status_never_calls_bank(self, fetch):
        output = StringIO()
        call_command("tochka_preflight", stdout=output)
        self.assertIn("NoSale local", output.getvalue())
        fetch.assert_not_called()
