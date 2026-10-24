from django.contrib.auth.models import User
from django.test import TestCase, override_settings

@override_settings(SAAS_PIN_AUTH_ENABLED=True, REQUIRE_EMAIL_VERIFICATION=True)
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
