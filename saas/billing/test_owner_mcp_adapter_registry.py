"""Owner R4.3: approved adapter registry is live on server and seller-safe."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant, OwnerMCPAdapter, OwnerMCPSettings
from guard.models import GuardAccount
from nosale_mcp.client import MCPToolUnavailable, call_owned_tool, definitions
from nosale_mcp.server import _read_allowed


@override_settings(
    SAAS_PIN_AUTH_ENABLED=False,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class OwnerMCPAdapterRegistryTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="mcp-registry-owner@example.test", password="test-only",
        )
        self.staff = User.objects.create_user(
            username="mcp-registry-staff@example.test",
            password="test-only", is_staff=True,
        )
        self.seller = User.objects.create_user(
            username="mcp-registry-seller@example.test", password="test-only",
        )
        self.policy = OwnerMCPSettings.runtime()

    def test_seeded_registry_contains_only_released_drivers(self):
        registry = {
            item.slug: (item.enabled, item.implementation)
            for item in OwnerMCPAdapter.objects.order_by("slug")
        }
        self.assertEqual(registry, {
            "wb": (True, "read_only_cached"),
            "ozon": (True, "status_only_unimplemented"),
        })
        self.assertTrue(self.policy.allows("wb_products"))
        self.assertTrue(self.policy.allows("ozon_adapter_status"))
        self.assertEqual(
            sorted(x["function"]["name"] for x in definitions("wb", policy=self.policy)),
            ["wb_events", "wb_overview", "wb_products", "wb_promotions"],
        )

    def test_admin_owner_can_disable_adapter_and_audit_change(self):
        self.client.force_login(self.owner)
        index = self.client.get("/admin/")
        self.assertEqual(index.status_code, 200)
        self.assertContains(index, reverse("admin:billing_ownermcpadapter_changelist"))
        url = reverse("admin:billing_ownermcpadapter_change", args=["wb"])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertContains(self.client.get(url), "Встроенный")
        # Checkbox deliberately omitted; the owner disables WB adapter.
        self.assertEqual(self.client.post(url, {"_save": "Save"}).status_code, 302)
        wb = OwnerMCPAdapter.objects.get(pk="wb")
        self.assertFalse(wb.enabled)
        self.assertEqual(wb.implementation, "read_only_cached")
        self.assertFalse(self.policy.allows("wb_products"))
        self.assertTrue(self.policy.allows("ozon_adapter_status"))
        self.assertEqual(definitions("wb", policy=self.policy), [])
        self.assertTrue(LogEntry.objects.filter(
            user=self.owner, content_type__model="ownermcpadapter",
            object_id="wb", action_flag=2,
        ).exists())
        self.assertContains(
            self.client.get(reverse("admin:admin_logentry_changelist")),
            "Wildberries",
        )

    def test_seller_and_staff_cannot_edit_or_view_registry(self):
        targets = (
            reverse("admin:billing_ownermcpadapter_changelist"),
            reverse("admin:billing_ownermcpadapter_change", args=["wb"]),
            reverse("admin:billing_ownermcpadapter_change", args=["ozon"]),
        )
        for user in (self.staff, self.seller):
            self.client.force_login(user)
            for url in targets:
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)
            self.assertNotEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_admin_cannot_add_delete_or_change_driver_implementation(self):
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.get(reverse("admin:billing_ownermcpadapter_add")).status_code, 403,
        )
        self.assertEqual(
            self.client.get(reverse("admin:billing_ownermcpadapter_delete", args=["wb"])).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse("admin:billing_ownermcpadapter_change", args=["ozon"]),
                {"enabled": "on", "slug": "anyhost", "implementation": "read_only_cached", "_save": "Save"},
            ).status_code,
            302,
        )
        ozon = OwnerMCPAdapter.objects.get(pk="ozon")
        self.assertEqual(ozon.implementation, "status_only_unimplemented")
        with self.assertRaises(ValueError):
            OwnerMCPAdapter(slug="https://127.0.0.1/mcp", enabled=True).save()
        self.assertEqual(OwnerMCPAdapter.objects.count(), 2)

    def test_direct_mcp_and_smart_cannot_bypass_disabled_adapter(self):
        shop = GuardAccount.objects.create(
            user=self.seller, marketplace="wb", name="Read only test WB",
        )
        AccessGrant.objects.create(
            user=self.seller, source="admin", source_ref="registry-test",
            plan="standard", starts_at=timezone.now()-timedelta(minutes=1),
            ends_at=timezone.now()+timedelta(hours=1),
        )
        claims = {"user_id": self.seller.pk, "store_id": shop.pk}
        wb = OwnerMCPAdapter.objects.get(pk="wb")
        wb.enabled = False
        wb.save(update_fields=["enabled"])
        with patch("nosale_mcp.client.issue_store_token") as mint, \
             patch("guard.wb.urlopen") as outbound:
            with self.assertRaises(MCPToolUnavailable):
                call_owned_tool(self.seller, shop, "wb_products", {"limit": 1})
            mint.assert_not_called()
            outbound.assert_not_called()
        with self.assertRaises(PermissionError):
            _read_allowed(claims, "wb_products", lambda _: {"items": ["should not be seen"]})
        self.assertTrue(self.policy.allows("ozon_adapter_status"))
        wb.enabled = True
        wb.save(update_fields=["enabled"])
        self.assertTrue(self.policy.allows("wb_products"))

    def test_ozon_placeholder_can_be_disabled_without_fake_api(self):
        ozon = OwnerMCPAdapter.objects.get(pk="ozon")
        self.assertEqual(ozon.implementation, "status_only_unimplemented")
        ozon.enabled = False
        ozon.save(update_fields=["enabled"])
        self.assertFalse(self.policy.allows("ozon_adapter_status"))
        self.assertEqual(definitions("ozon", policy=self.policy), [])
        self.assertTrue(self.policy.allows("wb_products"))

    def test_owner_status_dashboard_is_local_registry_not_secret_or_network(self):
        self.client.force_login(self.owner)
        with patch("billing.ai_platform_connection.build_opener") as ai, \
             patch("guard.wb.urlopen") as wb:
            r = self.client.get(reverse("nosale_owner_integrations"))
            ai.assert_not_called()
            wb.assert_not_called()
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Управление адаптерами")
        self.assertContains(r, "API не реализован")
        self.assertNotContains(r, "http://mcp:8099")
        self.assertEqual(r.context["readiness"]["mcp"]["adapters"], [
            {"slug": "ozon", "enabled": True, "implementation": "status_only_unimplemented"},
            {"slug": "wb", "enabled": True, "implementation": "read_only_cached"},
        ])
