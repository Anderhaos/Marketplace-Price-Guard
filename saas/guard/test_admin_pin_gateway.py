"""Admin must share NoSale's brute-force-protected PIN login, never Django's raw form."""
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import EmailPinLoginThrottle


@override_settings(
    SAAS_PIN_AUTH_ENABLED=True,
    REQUIRE_EMAIL_VERIFICATION=True,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class AdminPinGatewayTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_superuser(
            username="owner-pin@example.test", email="owner-pin@example.test",
            password="6184",
        )
        cls.seller = User.objects.create_user(
            username="seller-pin@example.test", email="seller-pin@example.test",
            password="7295",
        )

    def test_admin_redirects_to_single_pin_gateway(self):
        denied = self.client.get("/admin/")
        self.assertEqual(denied.status_code, 302)
        self.assertIn("/admin/login/", denied["Location"])
        login = self.client.get("/admin/login/?next=/admin/")
        self.assertRedirects(login, "/login/?next=/admin/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/login/?next=/admin/").status_code, 200)
        # This endpoint must not accept Django's unthrottled password POST.
        self.assertEqual(self.client.post("/admin/login/", {
            "username": self.owner.username, "password": "6184",
        }).status_code, 405)
        self.assertFalse(self.client.session.get("_auth_user_id"))

    def test_owner_can_enter_admin_via_existing_pin_flow(self):
        self.client.get("/login/?next=/admin/")
        self.assertEqual(self.client.post("/login/", {"email": self.owner.email}).status_code, 200)
        success = self.client.post("/login/", {"email": self.owner.email, "pin": "6184"})
        self.assertRedirects(success, "/admin/code/", fetch_redirect_response=False)
        self.assertNotEqual(self.client.get("/admin/").status_code, 200)

    def test_wrong_pin_hits_existing_persisted_lockout(self):
        self.client.get("/login/?next=/admin/")
        self.client.post("/login/", {"email": self.owner.email})
        for _ in range(5):
            self.assertEqual(self.client.post("/login/", {
                "email": self.owner.email, "pin": "0000",
            }).status_code, 200)
        throttle = EmailPinLoginThrottle.objects.get(email=self.owner.email)
        self.assertGreater(throttle.locked_until, timezone.now())
        self.client.post("/login/", {"email": self.owner.email, "pin": "6184"})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_nonstaff_never_enters_admin(self):
        self.client.get("/login/?next=/admin/")
        self.client.post("/login/", {"email": self.seller.email})
        success = self.client.post("/login/", {"email": self.seller.email, "pin": "7295"})
        self.assertRedirects(success, "/", fetch_redirect_response=False)
        # An authenticated seller must not get a second login form or admin access.
        self.assertEqual(self.client.get("/admin/login/").status_code, 403)
        denied = self.client.get("/admin/")
        self.assertContains(denied, "Панель владельца недоступна", status_code=403)
        self.assertContains(denied, 'action="/logout/"', status_code=403)
        self.assertNotContains(denied, "owner@svdev.pro", status_code=403)
        self.assertContains(denied, "csrfmiddlewaretoken", status_code=403)

    def test_external_next_url_never_redirects_credentials(self):
        for candidate in ("https://evil.example", "//evil.example", "/admin/../../evil"):
            with self.subTest(candidate=candidate):
                self.client.get("/login/?next=" + candidate)
                self.client.post("/login/", {"email": self.owner.email})
                self.assertRedirects(
                    self.client.post("/login/", {"email": self.owner.email, "pin": "6184"}),
                    "/", fetch_redirect_response=False,
                )
                self.client.post("/logout/")

    @override_settings(SAAS_PIN_AUTH_ENABLED=False)
    def test_legacy_admin_login_is_preserved(self):
        response = self.client.get("/admin/login/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "password", status_code=200)
