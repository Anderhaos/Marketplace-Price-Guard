"""Revocable 30-day owner device trust; the initial email-code gate is never bypassed."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from guard.email_pin import SESSION_OWNER_VERIFIED, TRUSTED_DEVICE_COOKIE
from guard.models import OwnerTrustedDevice


@override_settings(
    SAAS_PIN_AUTH_ENABLED=True,
    REQUIRE_EMAIL_VERIFICATION=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="NoSale <noreply@svdev.pro>",
    SESSION_COOKIE_SECURE=True,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class OwnerTrustedDeviceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="owner@example.test", email="owner@example.test", password="4371",
        )
        self.client.force_login(self.owner)
        self.ua = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/126.0.0.0"

    def _verify(self, remember=False):
        with patch("guard.email_pin.secrets.randbelow", return_value=42):
            self.assertEqual(self.client.get("/admin/code/", HTTP_USER_AGENT=self.ua).status_code, 200)
        payload = {"code": "0042"}
        if remember:
            payload["remember_device"] = "yes"
        return self.client.post("/admin/code/", payload, HTTP_USER_AGENT=self.ua)

    def _expire_admin_stepup(self):
        session = self.client.session
        session[SESSION_OWNER_VERIFIED] = {
            "user_id": self.owner.pk,
            "valid_until": (timezone.now() - timedelta(seconds=1)).timestamp(),
        }
        session.save()

    def test_checkbox_absent_never_trusts_device(self):
        response = self._verify()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(OwnerTrustedDevice.objects.count(), 0)
        self.assertNotIn(TRUSTED_DEVICE_COOKIE, response.cookies)
        self._expire_admin_stepup()
        self.assertNotEqual(self.client.get("/admin/").status_code, 200)

    def test_verified_device_stays_trusted_after_short_admin_expiry(self):
        response = self._verify(remember=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/admin/")
        self.assertIn(TRUSTED_DEVICE_COOKIE, response.cookies)
        cookie = response.cookies[TRUSTED_DEVICE_COOKIE]
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(int(cookie["max-age"]), 2592000)
        trusted = OwnerTrustedDevice.objects.get(user=self.owner)
        self.assertNotEqual(trusted.token_digest, cookie.value)
        self.assertEqual(len(trusted.token_digest), 64)
        self.assertEqual(trusted.label, "Chrome · Windows")
        self.assertGreater(self.client.session.get_expiry_age(), 2591900)
        self.assertLessEqual(self.client.session.get_expiry_age(), 2592000)
        self._expire_admin_stepup()
        self.assertEqual(self.client.get("/admin/", HTTP_USER_AGENT=self.ua).status_code, 200)

    def test_different_browser_cannot_bypass_email_and_pin(self):
        self._verify(remember=True)
        self._expire_admin_stepup()
        self.assertNotEqual(self.client.get("/admin/", HTTP_USER_AGENT="Firefox/127").status_code, 200)
        other = Client()
        other.force_login(self.owner)
        self.assertNotEqual(other.get("/admin/", HTTP_USER_AGENT=self.ua).status_code, 200)

    def test_trust_invalidated_by_pin_change_or_expiry(self):
        self._verify(remember=True)
        self._expire_admin_stepup()
        OwnerTrustedDevice.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertNotEqual(self.client.get("/admin/", HTTP_USER_AGENT=self.ua).status_code, 200)
        OwnerTrustedDevice.objects.update(expires_at=timezone.now() + timedelta(days=2))
        self.owner.set_password("8675")
        self.owner.save(update_fields=["password"])
        self.client.force_login(self.owner)
        self.assertNotEqual(self.client.get("/admin/", HTTP_USER_AGENT=self.ua).status_code, 200)

    def test_logout_revokes_current_browser_approval(self):
        self._verify(remember=True)
        response = self.client.post("/logout/", HTTP_USER_AGENT=self.ua)
        self.assertIn(response.status_code, (200, 302))
        self.assertEqual(OwnerTrustedDevice.objects.count(), 0)
        self.assertEqual(response.cookies[TRUSTED_DEVICE_COOKIE]["max-age"], 0)
        self.client.force_login(self.owner)
        self.assertNotEqual(self.client.get("/admin/", HTTP_USER_AGENT=self.ua).status_code, 200)

    def test_owner_can_revoke_device_and_seller_cannot(self):
        self._verify(remember=True)
        device = OwnerTrustedDevice.objects.get(user=self.owner)
        response = self.client.get("/admin/devices/", HTTP_USER_AGENT=self.ua)
        self.assertContains(response, "Chrome · Windows")
        self.assertContains(response, "это устройство")
        response = self.client.post("/admin/devices/", {"device_id": str(device.pk)}, HTTP_USER_AGENT=self.ua)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(OwnerTrustedDevice.objects.count(), 0)
        self.assertEqual(response.cookies[TRUSTED_DEVICE_COOKIE]["max-age"], 0)
        self._expire_admin_stepup()
        self.assertNotEqual(self.client.get("/admin/", HTTP_USER_AGENT=self.ua).status_code, 200)
        seller = User.objects.create_user(username="seller@example.test", password="1111")
        self.client.force_login(seller)
        self.assertEqual(self.client.get("/admin/devices/").status_code, 403)

    def test_bad_code_never_registers_device(self):
        with patch("guard.email_pin.secrets.randbelow", return_value=42):
            self.client.get("/admin/code/", HTTP_USER_AGENT=self.ua)
        response = self.client.post(
            "/admin/code/", {"code": "9999", "remember_device": "yes"},
            HTTP_USER_AGENT=self.ua,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(OwnerTrustedDevice.objects.count(), 0)
        self.assertNotIn(TRUSTED_DEVICE_COOKIE, response.cookies)
