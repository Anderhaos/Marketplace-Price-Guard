"""Platform owner and seller are separate login destinations and privileges."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import GuardAccount


@override_settings(
    SAAS_PIN_AUTH_ENABLED=True,
    SECURE_SSL_REDIRECT=False,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class PlatformOwnerSeparationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_superuser(
            username="office@example.test", email="office@example.test",
            password="4839",
        )
        self.seller = User.objects.create_user(
            username="seller@example.test", email="seller@example.test",
            password="6742",
        )
        GuardAccount.objects.create(user=self.seller)

    def test_owner_root_and_login_route_to_admin_not_seller_setup(self):
        self.client.force_login(self.owner)
        self.assertRedirects(self.client.get("/"), "/admin/", fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/login/"), "/admin/", fetch_redirect_response=False)
        self.assertRedirects(
            self.client.get("/connect/wb/setup/"), "/admin/",
            fetch_redirect_response=False,
        )
        self.assertFalse(GuardAccount.objects.filter(user=self.owner).exists())
        self.assertNotEqual(self.client.get("/admin/").status_code, 200)

    def test_owner_pin_login_requires_owner_email_stepup(self):
        self.assertEqual(self.client.post("/login/", {"email":self.owner.email}).status_code, 200)
        response=self.client.post(
            "/login/", {"email":self.owner.email, "pin":"4839"},
        )
        self.assertRedirects(response, "/admin/code/", fetch_redirect_response=False)
        self.assertFalse(GuardAccount.objects.filter(user=self.owner).exists())

    def test_seller_is_not_admin_and_stays_in_seller_onboarding(self):
        self.client.force_login(self.seller)
        response=self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Добавим ваш магазин")
        self.assertEqual(self.client.get("/admin/code/").status_code, 403)
        self.assertNotEqual(self.client.get("/admin/").status_code, 200)
        self.assertTrue(GuardAccount.objects.filter(user=self.seller).exists())
