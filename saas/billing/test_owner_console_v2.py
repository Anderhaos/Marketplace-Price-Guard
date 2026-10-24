"""Owner Console v2 regression: live forms, isolation, audits and safe navigation."""
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from billing.models import (
    OwnerAISettings, OwnerAIGatewayConnection, OwnerMCPAdapter,
    OwnerMCPSettings, OwnerOperationsSettings, OwnerTariffOffer,
)


@override_settings(
    SAAS_PIN_AUTH_ENABLED=False,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class OwnerConsoleV2Tests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="console-owner@example.test", email="console-owner@example.test",
            password="only-in-tests",
        )
        self.seller = User.objects.create_user(
            username="console-seller@example.test", password="only-in-tests",
        )
        self.staff = User.objects.create_user(
            username="console-staff@example.test", password="only-in-tests",
            is_staff=True,
        )

    def url(self, section):
        return reverse("nosale_owner_section", args=[section])

    def test_anonymous_and_nonowners_blocked_from_every_panel_and_post(self):
        urls = [reverse("nosale_owner_home")] + [
            self.url(s) for s in ("ai", "mcp", "operations", "tariffs")
        ]
        for url in urls:
            self.assertNotEqual(self.client.get(url).status_code, 200)
        for user in (self.seller, self.staff):
            self.client.force_login(user)
            for url in urls:
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)
                    self.assertEqual(self.client.post(url, {"_panel": "mcp"}).status_code, 403)

    def test_no_get_network_or_secret_render_on_dashboard(self):
        self.client.force_login(self.owner)
        OwnerAIGatewayConnection.objects.create(
            encrypted_service_token="SECRET_NEVER_EXPOSE_HERE",
            last_check_ok=True, catalog_models=["approved-model"],
        )
        with patch("billing.ai_platform_connection.build_opener") as network, \
             patch("guard.crypto.decrypt_token") as decrypt:
            for url in ("/admin/", self.url("ai"), self.url("mcp"),
                        self.url("operations"), self.url("tariffs")):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 200)
                self.assertNotContains(r, "SECRET_NEVER_EXPOSE_HERE")
                self.assertContains(r, "owner-console.css")
            network.assert_not_called()
            decrypt.assert_not_called()

    def test_mcp_update_persists_and_generates_safe_audit(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url("mcp"), {
            "_panel": "mcp",
            "mcp-enabled": "on", "mcp-wb_enabled": "on",
            "mcp-wb_overview_enabled": "on",
            "mcp-wb_events_enabled": "on",
            "mcp-wb_promotions_enabled": "on",
            "mcp-ozon_status_enabled": "on",
            # Intentionally omit wb_products_enabled.
            "mcp-max_products": "8", "mcp-max_events": "4",
            "mcp-max_promotions": "5",
        })
        self.assertEqual(response.status_code, 302)
        settings = OwnerMCPSettings.runtime()
        self.assertFalse(settings.wb_products_enabled)
        self.assertEqual(settings.max_events, 4)
        entry = LogEntry.objects.filter(user=self.owner, content_type__model="ownermcpsettings").first()
        self.assertIsNotNone(entry)
        self.assertNotIn("max_events", entry.change_message)
        self.assertNotIn("SECRET", entry.change_message)

    def test_invalid_settings_fail_closed_without_audit(self):
        self.client.force_login(self.owner)
        before = OwnerMCPSettings.runtime().max_products
        r = self.client.post(self.url("mcp"), {
            "_panel": "mcp", "mcp-max_products": "999",
            "mcp-max_events": "1", "mcp-max_promotions": "1",
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "999")
        self.assertEqual(OwnerMCPSettings.runtime().max_products, before)
        self.assertFalse(LogEntry.objects.filter(user=self.owner).exists())

    def test_adapter_toggle_is_only_known_driver(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.url("mcp"), {
            "_panel": "adapter_wb",
        }).status_code, 302)
        self.assertFalse(OwnerMCPAdapter.permits("wb"))
        self.assertFalse(OwnerMCPSettings.runtime().allows("wb_products"))
        self.assertTrue(OwnerMCPAdapter.permits("ozon"))
        self.assertEqual(self.client.post(self.url("mcp"), {
            "_panel": "adapter_fake",
        }).status_code, 400)
        self.assertEqual(OwnerMCPAdapter.objects.count(), 2)

    def test_operations_update_is_real_and_bounded(self):
        self.client.force_login(self.owner)
        r = self.client.post(self.url("operations"), {
            "_panel": "operations", "operations-guard_poll_seconds": "15",
            "operations-notifications_poll_seconds": "30",
            "operations-push_batch_size": "12",
        })
        self.assertEqual(r.status_code, 302)
        ops = OwnerOperationsSettings.runtime()
        self.assertEqual(ops.guard_poll_seconds, 15)
        self.assertFalse(ops.guard_checks_enabled)
        self.assertFalse(ops.push_delivery_enabled)

    def test_tariff_edit_keeps_existing_identity_and_does_not_create_fake_offer(self):
        self.client.force_login(self.owner)
        before = OwnerTariffOffer.objects.count()
        r = self.client.post(self.url("tariffs"), {
            "_panel": "tariff_standard", "tariff_standard-name": "Standard",
            "tariff_standard-price_rub": "599.00", "tariff_standard-enabled": "on",
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(OwnerTariffOffer.objects.count(), before)
        offer = OwnerTariffOffer.objects.get(pk="standard")
        self.assertEqual(offer.price_kopeks, 59900)
        self.assertTrue(offer.enabled)
        self.assertEqual(self.client.post(self.url("tariffs"), {
            "_panel": "tariff_fake", "price_rub": "1",
        }).status_code, 400)

    def test_gateway_save_does_not_leak_or_verify_without_explicit_action(self):
        self.client.force_login(self.owner)
        with patch("billing.owner_console.verify_gateway") as verify:
            r = self.client.post(self.url("ai"), {
                "_panel": "gateway",
                "gateway-base_url": "https://platform.svdev.pro/v1",
                "gateway-catalog_mode": "scoped",
                "gateway-service_token": "secret_key_abcdefghijklmnopqrstuvwxyz",
            })
            self.assertEqual(r.status_code, 302)
            verify.assert_not_called()
        conn = OwnerAIGatewayConnection.objects.get(pk=1)
        self.assertTrue(conn.configured)
        self.assertNotEqual(conn.encrypted_service_token, "secret_key_abcdefghijklmnopqrstuvwxyz")
        self.assertNotContains(self.client.get(self.url("ai")), "secret_key_abcdefghijklmnopqrstuvwxyz")

    def test_provider_verification_is_explicit_post_and_audited(self):
        self.client.force_login(self.owner)
        OwnerAIGatewayConnection.objects.create(encrypted_service_token="synthetic-test-token")
        with patch("billing.owner_console.verify_gateway", return_value=["chat-a"]) as verify:
            self.assertEqual(self.client.get(self.url("ai")).status_code, 200)
            verify.assert_not_called()
            self.assertEqual(self.client.post(self.url("ai"), {
                "_panel": "verify-gateway",
            }).status_code, 302)
            verify.assert_called_once()
        self.assertTrue(LogEntry.objects.filter(
            user=self.owner, content_type__model="owneraigatewayconnection",
        ).exists())

    def test_unknown_sections_and_wrong_action_are_not_saved(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url("not-a-section")).status_code, 404)
        self.assertEqual(self.client.post(self.url("ai"), {
            "_panel": "mcp",
        }).status_code, 400)
        self.assertFalse(LogEntry.objects.filter(user=self.owner).exists())
