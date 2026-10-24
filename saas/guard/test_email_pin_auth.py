import re
from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import EmailPinChallenge, EmailPinLoginThrottle, GuardAccount


@override_settings(
    SAAS_PIN_AUTH_ENABLED=True,
    REQUIRE_EMAIL_VERIFICATION=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="MP NoSale <noreply@svdev.pro>",
)
class EmailPinAuthTests(TestCase):
    def _send(self, email="seller@example.test", url="/register/"):
        response = self.client.post(url, {"email": email})
        self.assertRedirects(response, "/auth/code/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("noreply@svdev.pro", mail.outbox[0].from_email)
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        self.assertEqual(mail.outbox[0].alternatives[0].mimetype, "text/html")
        self.assertIn("MP NoSale", mail.outbox[0].alternatives[0].content)
        code = re.search(r"Код: (\d{4})", mail.outbox[0].body)
        self.assertIsNotNone(code)
        return code.group(1)

    def _create(self, email="seller@example.test", pin="6184"):
        code = self._send(email)
        self.assertRedirects(self.client.post("/auth/code/", {"code": code}), "/auth/pin/")
        self.assertRedirects(
            self.client.post("/auth/pin/", {"pin": pin, "confirm_pin": pin}),
            "/",
        )
        user = User.objects.get(email=email)
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password(pin))
        self.assertEqual(GuardAccount.objects.filter(user=user).count(), 1)
        return user

    def test_registration_code_pin_and_authenticated_dashboard(self):
        self._create()
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertFalse(EmailPinChallenge.objects.exists())
        self.assertTrue(self.client.session.get("_auth_user_id"))

    def test_code_wrong_five_times_blocks_then_correct_code_still_rejected(self):
        code = self._send()
        for unused in range(5):
            wrong = "0000" if code != "0000" else "9999"
            self.assertEqual(self.client.post("/auth/code/", {"code": wrong}).status_code, 200)
        self.assertEqual(EmailPinChallenge.objects.get().attempts, 5)
        self.assertEqual(self.client.post("/auth/code/", {"code": code}).status_code, 200)
        self.assertEqual(self.client.get("/auth/pin/").status_code, 302)
        self.assertFalse(User.objects.filter(email="seller@example.test").exists())

    def test_resend_cooldown_does_not_issue_new_code_or_reset_attempts(self):
        self._send()
        challenge = EmailPinChallenge.objects.get()
        self.client.post("/auth/code/", {"code": "9999"})
        self.client.post("/register/", {"email": "seller@example.test"})
        self.assertEqual(len(mail.outbox), 1)
        challenge.refresh_from_db()
        self.assertEqual(challenge.attempts, 1)

    def test_reset_pin_using_email_code(self):
        self._create()
        self.client.post("/logout/")
        mail.outbox.clear()
        code = self._send(url="/password-reset/")
        self.assertRedirects(self.client.post("/auth/code/", {"code": code}), "/auth/pin/")
        self.assertRedirects(self.client.post(
            "/auth/pin/", {"pin": "8956", "confirm_pin": "8956"},
        ), "/login/")
        self.assertTrue(User.objects.get(email="seller@example.test").check_password("8956"))
        self.assertFalse(User.objects.get(email="seller@example.test").check_password("6184"))
        self.assertRedirects(self.client.post("/login/", {
            "email": "seller@example.test", "pin": "8956",
        }), "/")

    def test_recovery_unknown_email_never_sends(self):
        result = self.client.post("/password-reset/", {"email": "missing@example.test"})
        self.assertRedirects(result, "/auth/code/")
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.client.post("/auth/code/", {"code": "0000"}).status_code, 200)

    def test_login_pin_locks_out_after_five_failed_attempts(self):
        self._create()
        self.client.post("/logout/")
        for unused in range(5):
            self.assertEqual(self.client.post("/login/", {
                "email": "seller@example.test", "pin": "0000",
            }).status_code, 200)
        throttle = EmailPinLoginThrottle.objects.get(email="seller@example.test")
        self.assertEqual(throttle.attempts, 5)
        self.assertTrue(throttle.locked_until > timezone.now())
        self.assertEqual(self.client.post("/login/", {
            "email": "seller@example.test", "pin": "6184",
        }).status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_simple_pin_rejected(self):
        self._send()
        code = re.search(r"Код: (\d{4})", mail.outbox[0].body).group(1)
        self.client.post("/auth/code/", {"code": code})
        self.assertEqual(self.client.post("/auth/pin/", {
            "pin": "1111", "confirm_pin": "1111",
        }).status_code, 200)
        self.assertFalse(User.objects.filter(email="seller@example.test").exists())


    def test_otp_preserves_leading_zeroes(self):
        from unittest.mock import patch
        with patch("guard.email_pin.secrets.randbelow", return_value=7):
            code = self._send()
        self.assertEqual(code, "0007")
        self.assertRedirects(self.client.post("/auth/code/", {"code": code}), "/auth/pin/")

    def test_new_pin_requires_exactly_four_digits(self):
        self._send()
        code = re.search(r"Код: (\d{4})", mail.outbox[0].body).group(1)
        self.client.post("/auth/code/", {"code": code})
        result = self.client.post("/auth/pin/", {
            "pin": "123456", "confirm_pin": "123456",
        })
        self.assertEqual(result.status_code, 200)
        self.assertFalse(User.objects.filter(email="seller@example.test").exists())
