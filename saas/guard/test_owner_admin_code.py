from django.contrib.auth.models import User
from django.test import TestCase, override_settings

@override_settings(SAAS_PIN_AUTH_ENABLED=True, REQUIRE_EMAIL_VERIFICATION=True, STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class OwnerAdminStepupTests(TestCase):
    def test_superuser_needs_second_factor(self):
        user = User.objects.create_superuser(username="admin@example.test", email="admin@example.test", password="exampletest")
        self.client.force_login(user)
        self.assertNotEqual(self.client.get("/admin/").status_code, 200)

    def test_regular_user_cannot_reach_code_screen(self):
        user = User.objects.create_user(username="seller@example.test", email="seller@example.test", password="exampletest")
        self.client.force_login(user)
        self.assertEqual(self.client.get("/admin/code/").status_code, 403)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="NoSale <noreply@svdev.pro>",
    )
    def test_owner_stepup_success_using_synthetic_fixture(self):
        from unittest.mock import patch
        user = User.objects.create_superuser(
            username="verified-owner@example.test", email="verified-owner@example.test",
            password="test-only",
        )
        self.client.force_login(user)
        with patch("guard.email_pin.secrets.randbelow", return_value=42):
            self.assertEqual(self.client.get("/admin/code/").status_code, 200)
        response = self.client.post("/admin/code/", {"code": "0042"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="NoSale <noreply@svdev.pro>",
    )
    def test_retry_limit_and_single_use(self):
        from unittest.mock import patch
        from guard.models import EmailPinChallenge
        from django.core import mail
        user = User.objects.create_superuser(
            username="retry-owner@example.test", email="retry-owner@example.test",
            password="synthetic-only",
        )
        self.client.force_login(user)
        with patch("guard.email_pin.secrets.randbelow", return_value=42):
            self.client.get("/admin/code/")
        self.assertEqual(len(mail.outbox), 1)
        for attempt in range(5):
            self.assertEqual(self.client.post("/admin/code/", {"code": "9999"}).status_code, 200)
        challenge = EmailPinChallenge.objects.get(purpose="owner")
        self.assertEqual(challenge.attempts, 5)
        self.assertNotEqual(self.client.post("/admin/code/", {"code": "0042"}).status_code, 302)
        self.client.post("/admin/code/", {"action": "resend"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotEqual(self.client.get("/admin/").status_code, 200)
