"""PIN recovery: verified email + new PIN must establish a seller session.

All cases are synthetic, isolated in Django's test DB, and never send mail.
"""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.utils import timezone

from .email_pin import SESSION_CHALLENGE
from .models import EmailPinChallenge


@override_settings(
    SAAS_PIN_AUTH_ENABLED=True,
    REQUIRE_EMAIL_VERIFICATION=True,
    SAAS_ACCESS_ENFORCED=False,
    SAAS_WB_NETWORK_ENABLED=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class PinRecoveryAutoLoginTests(TestCase):
    email = "seller-recovery@example.test"
    old_pin = "2784"
    new_pin = "5937"

    def setUp(self):
        self.user = User.objects.create_user(
            username=self.email, email=self.email, password=self.old_pin,
        )

    def _verified_recovery(self):
        """Create a synthetic verified challenge without mailing a real address."""
        challenge = EmailPinChallenge.objects.create(
            email=self.email,
            purpose=EmailPinChallenge.PURPOSE_RECOVER,
            code_hash="",
            sent_at=timezone.now(),
            expires_at=timezone.now() + timedelta(minutes=10),
            verified_until=timezone.now() + timedelta(minutes=5),
        )
        session = self.client.session
        session[SESSION_CHALLENGE] = challenge.pk
        session.save()
        return challenge

    def test_full_email_code_to_pin_reset_logs_in_without_second_login(self):
        with patch("guard.email_pin.send_pin_verification_email") as sender:
            issued = self.client.post("/password-reset/", {"email": self.email})
        self.assertEqual(issued.status_code, 302)
        self.assertEqual(issued.url, "/auth/code/")
        sender.assert_called_once()
        code = sender.call_args.kwargs["code"]
        self.assertRegex(code, r"^\d{4}$")

        verified = self.client.post("/auth/code/", {"code": code})
        self.assertEqual(verified.status_code, 302)
        self.assertEqual(verified.url, "/auth/pin/")

        completed = self.client.post(
            "/auth/pin/",
            {"pin": self.new_pin, "confirm_pin": self.new_pin},
        )
        self.assertEqual(completed.status_code, 302)
        self.assertEqual(completed.url, "/")  # dashboard, never /login/
        self.assertEqual(
            self.client.session.get("_auth_user_id"), str(self.user.pk),
        )
        self.assertNotIn(SESSION_CHALLENGE, self.client.session)
        self.assertFalse(EmailPinChallenge.objects.filter(
            email=self.email, purpose=EmailPinChallenge.PURPOSE_RECOVER,
        ).exists())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.new_pin))
        self.assertFalse(self.user.check_password(self.old_pin))

        with patch("guard.views.dashboard", return_value=HttpResponse("seller dashboard")):
            after = self.client.get("/")
        self.assertEqual(after.status_code, 200)
        self.assertContains(after, "seller dashboard")

        # Re-submission after success cannot issue a second reset.
        retry = self.client.post(
            "/auth/pin/", {"pin": "6825", "confirm_pin": "6825"},
        )
        self.assertEqual(retry.status_code, 302)
        self.assertEqual(retry.url, "/")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.new_pin))

    def test_mismatched_pin_does_not_change_password_or_log_in(self):
        challenge = self._verified_recovery()
        result = self.client.post(
            "/auth/pin/", {"pin": self.new_pin, "confirm_pin": "6825"},
        )
        self.assertEqual(result.status_code, 200)
        self.assertIn("confirm_pin", result.context["form"].errors)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(EmailPinChallenge.objects.filter(pk=challenge.pk).exists())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.old_pin))

    def test_expired_verification_cannot_reset_or_log_in(self):
        challenge = self._verified_recovery()
        challenge.verified_until = timezone.now() - timedelta(seconds=10)
        challenge.save(update_fields=["verified_until"])
        response = self.client.post(
            "/auth/pin/", {"pin": self.new_pin, "confirm_pin": self.new_pin},
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.old_pin))

    def test_inactive_account_cannot_be_automatically_logged_in(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        challenge = self._verified_recovery()
        response = self.client.post(
            "/auth/pin/", {"pin": self.new_pin, "confirm_pin": self.new_pin},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(EmailPinChallenge.objects.filter(pk=challenge.pk).exists())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.old_pin))
