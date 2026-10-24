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
