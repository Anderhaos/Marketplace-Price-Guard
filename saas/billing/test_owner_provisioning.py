"""Secure owner provisioning is never an unauthenticated web feature."""
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse


class OwnerProvisioningTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="registered-owner@example.test",
            email="registered-owner@example.test",
            password="test-only-pin",
            is_active=True,
        )

    def call(self, *, email="registered-owner@example.test", confirm=False):
        output = StringIO()
        call_command(
            "provision_owner_admin", email=email, confirm=confirm, stdout=output
        )
        return output.getvalue()

    def test_no_server_allowlist_never_elevates(self):
        with patch.dict("os.environ", {"NOSALE_OWNER_EMAIL": ""}):
            with self.assertRaises(CommandError):
                self.call(confirm=True)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_superuser)

    def test_wrong_email_never_elevates(self):
        with patch.dict("os.environ", {"NOSALE_OWNER_EMAIL": "other-owner@example.test"}):
            with self.assertRaises(CommandError):
                self.call(confirm=True)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_superuser)

    def test_nonexistent_email_never_creates_account(self):
        with patch.dict("os.environ", {"NOSALE_OWNER_EMAIL": "missing-owner@example.test"}):
            with self.assertRaises(CommandError):
                self.call(email="missing-owner@example.test", confirm=True)
        self.assertEqual(User.objects.count(), 1)

    def test_inactive_or_unset_pin_never_elevates(self):
        with patch.dict("os.environ", {"NOSALE_OWNER_EMAIL": self.user.email}):
            self.user.is_active = False
            self.user.save(update_fields=["is_active"])
            with self.assertRaises(CommandError):
                self.call(confirm=True)
            self.user.is_active = True
            self.user.set_unusable_password()
            self.user.save(update_fields=["is_active", "password"])
            with self.assertRaises(CommandError):
                self.call(confirm=True)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)

    def test_registered_address_must_match_user_login(self):
        self.user.username = "unexpected-alias"
        self.user.save(update_fields=["username"])
        with patch.dict("os.environ", {"NOSALE_OWNER_EMAIL": self.user.email}):
            with self.assertRaises(CommandError):
                self.call(confirm=True)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_superuser)

    def test_duplicate_email_fails_closed(self):
        User.objects.create_user(
            username="duplicate-user@example.test",
            email=self.user.email,
            password="test-only",
            is_active=True,
        )
        with patch.dict("os.environ", {"NOSALE_OWNER_EMAIL": self.user.email}):
            with self.assertRaises(CommandError):
                self.call(confirm=True)
        self.assertFalse(User.objects.filter(is_superuser=True).exists())

    def test_dry_run_idempotence_and_actual_admin_access(self):
        url = reverse("admin:index")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 302)
        with patch.dict("os.environ", {"NOSALE_OWNER_EMAIL": self.user.email}):
            self.assertIn("Run with --confirm", self.call())
            self.user.refresh_from_db()
            self.assertFalse(self.user.is_staff)
            self.assertIn("rights granted", self.call(confirm=True))
            self.assertIn("already active", self.call(confirm=True))
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_superuser)
        self.assertTrue(self.user.is_staff)
        with override_settings(
            STORAGES={
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            }
        ):
            self.assertEqual(self.client.get(url).status_code, 200)
