import json
from io import BytesIO
from unittest.mock import patch
from urllib.error import URLError

from django.core.mail import EmailMessage
from django.test import SimpleTestCase, override_settings

from portal.email_backend import RelayEmailBackend


class FakeResponse:
    status = 202
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


@override_settings(
    MAIL_RELAY_URL="https://mail.beclients.ru/svdev-relay.php",
    MAIL_RELAY_TOKEN="sample-test-token-with-at-least-32-chars-000",
    DEFAULT_FROM_EMAIL="SVDEV <noreply@svdev.pro>",
)
class RelayBackendTests(SimpleTestCase):
    @patch("portal.email_backend.build_opener", return_value=FakeResponse())
    def test_sends_over_https_with_provider_protocol(self, transport):
        message = EmailMessage("Код входа", "Ваш код: 4821", "noreply@svdev.pro", ["seller@example.com"])
        self.assertEqual(RelayEmailBackend().send_messages([message]), 1)
        request = transport.call_args.args[0]
        self.assertEqual(request.full_url, "https://mail.beclients.ru/svdev-relay.php")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("X-svdev-relay-token"), "sample-test-token-with-at-least-32-chars-000")
        self.assertEqual(transport.call_args.kwargs["timeout"], 12)
        payload = json.loads(request.data)
        self.assertEqual(payload["kind"], "codes")
        self.assertEqual(payload["to"], "seller@example.com")
        self.assertEqual(payload["subject"], "Код входа")
        self.assertEqual(payload["text"], "Ваш код: 4821")

    @patch("portal.email_backend.urlopen")
    def test_empty_no_send(self, transport):
        self.assertEqual(RelayEmailBackend().send_messages([]), 0)
        transport.assert_not_called()

    @override_settings(MAIL_RELAY_URL="https://127.0.0.1/private")
    @patch("portal.email_backend.urlopen")
    def test_rejects_arbitrary_internal_urls(self, transport):
        message = EmailMessage("Code", "code", "noreply@svdev.pro", ["a@example.com"])
        with self.assertRaises(ValueError):
            RelayEmailBackend().send_messages([message])
        transport.assert_not_called()

    @override_settings(MAIL_RELAY_URL="https://mail.beclients.ru:444/svdev-relay.php")
    def test_rejects_unapproved_port(self):
        with self.assertRaises(ValueError):
            RelayEmailBackend().send_messages([
                EmailMessage("Code", "code", "noreply@svdev.pro", ["a@example.com"])
            ])

    @override_settings(MAIL_RELAY_TOKEN="")
    def test_requires_independent_token(self):
        with self.assertRaises(ValueError):
            RelayEmailBackend().send_messages([
                EmailMessage("Code", "code", "noreply@svdev.pro", ["a@example.com"])
            ])

    def test_sender_is_fixed_to_configured_svdev_identity(self):
        with self.assertRaises(ValueError):
            RelayEmailBackend().send_messages([
                EmailMessage("Code", "code", "admin@another-domain.test", ["a@example.com"])
            ])

    @patch("portal.email_backend.urlopen", side_effect=URLError("blocked"))
    def test_transport_error_fails_closed(self, transport):
        message = EmailMessage("Code", "code", "noreply@svdev.pro", ["a@example.com"])
        with self.assertRaises(URLError):
            RelayEmailBackend().send_messages([message])
        self.assertEqual(RelayEmailBackend(fail_silently=True).send_messages([message]), 0)

    def test_no_attachment_silently_dropped(self):
        message = EmailMessage("Code", "code", "noreply@svdev.pro", ["a@example.com"])
        message.attach("file.txt", "content", "text/plain")
        with self.assertRaises(ValueError):
            RelayEmailBackend().send_messages([message])
