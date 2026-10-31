"""Production SMTP transport safety: reject unencrypted or spoofed sender."""
import os
import subprocess
import sys

from django.test import SimpleTestCase


class SecureSmtpSettingsTests(SimpleTestCase):
    def _check(self, **overrides):
        env = os.environ.copy()
        # CI does not export the Django secret to test subprocesses.
        # Use independent, synthetic, correctly formatted keys in tests.
        from cryptography.fernet import Fernet
        env.update({
            "DJANGO_SECRET_KEY": "smtp-setting-tests-only-not-a-production-secret",
            "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
            "DJANGO_DEBUG": "0",
            "SAAS_PIN_AUTH_ENABLED": "1",
            "REQUIRE_EMAIL_VERIFICATION": "1",
            "DJANGO_EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "SMTP_HOST": "mail.hosting.reg.ru",
            "SMTP_PORT": "465",
            "SMTP_USE_TLS": "0",
            "SMTP_USE_SSL": "1",
            "SMTP_TIMEOUT": "12",
            "SMTP_USER": "noreply@nosale.pro",
            "SMTP_PASSWORD": "sample-test-password-only",
            "SMTP_FROM": "NoSale <noreply@nosale.pro>",
            "DJANGO_SETTINGS_MODULE": "portal.settings",
        })
        env.update(overrides)
        return subprocess.run(
            [sys.executable, "-c", "import django; django.setup()"],
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_authenticated_ssl_on_465_is_accepted(self):
        result = self._check()
        self.assertEqual(result.returncode, 0, result.stderr[-750:])

    def test_authenticated_starttls_on_587_is_accepted(self):
        result = self._check(SMTP_PORT="587", SMTP_USE_SSL="0", SMTP_USE_TLS="1")
        self.assertEqual(result.returncode, 0, result.stderr[-750:])

    def test_plaintext_smtp_is_rejected(self):
        result = self._check(SMTP_USE_TLS="0", SMTP_USE_SSL="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("authenticated TLS/SSL SMTP", result.stderr)

    def test_sender_must_match_authenticated_mailbox(self):
        result = self._check(SMTP_FROM="NoSale <another@nosale.pro>")
        self.assertNotEqual(result.returncode, 0)

    def test_missing_smtp_password_is_rejected(self):
        result = self._check(SMTP_PASSWORD="")
        self.assertNotEqual(result.returncode, 0)

    def test_legacy_https_relay_remains_supported(self):
        result = self._check(
            DJANGO_EMAIL_BACKEND="portal.email_backend.RelayEmailBackend",
            MAIL_RELAY_URL="https://mail.beclients.ru/svdev-relay.php",
            MAIL_RELAY_TOKEN="sample-token-1234567890-1234567890-12345",
        )
        self.assertEqual(result.returncode, 0, result.stderr[-750:])
