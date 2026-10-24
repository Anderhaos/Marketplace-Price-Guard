"""Owner-only SaaS admin plus live persisted MCP tool switches."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant, OwnerMCPSettings
from guard.models import GuardAccount
from nosale_mcp.client import MCPToolUnavailable, call_owned_tool, definitions
from nosale_mcp.server import _read_allowed


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    SAAS_PIN_AUTH_ENABLED=False,
)
class OwnerControlPlaneTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="owner-panel@example.test", email="owner-panel@example.test", password="test-pass",
        )
        self.staff = User.objects.create_user(
            username="staff-panel@example.test", password="test-pass", is_staff=True,
        )
        self.seller = User.objects.create_user(username="seller-panel@example.test", password="test-pass")
        self.mcp_settings = OwnerMCPSettings.runtime()
        self.assertIsNotNone(self.mcp_settings)

    def test_owner_dashboard_links_all_system_controls(self):
        self.client.force_login(self.owner)
        r = self.client.get("/admin/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Панель владельца")
        for label in (
            "MCP и площадки", "AI Platform", "Модели и лимиты Smart",
            "Тарифы", "Клиенты и подписки", "Мониторинг",
        ):
            self.assertContains(r, label)
        self.assertContains(r, reverse("admin:billing_ownermcpsettings_changelist"))
        self.assertContains(r, reverse("admin:billing_owneraisettings_changelist"))
        self.assertContains(r, reverse("admin:billing_owneraigatewayconnection_changelist"))

    def test_staff_and_sellers_cannot_open_any_admin_page(self):
        targets = (
            "/admin/", reverse("admin:billing_ownermcpsettings_changelist"),
            reverse("admin:billing_owneraisettings_changelist"),
            reverse("admin:billing_owneraigatewayconnection_changelist"),
            reverse("admin:auth_user_changelist"),
            reverse("admin:guard_guardevent_changelist"),
        )
        for user in (self.staff, self.seller):
            self.client.force_login(user)
            for path in targets:
                with self.subTest(user=user.username, path=path):
                    result = self.client.get(path)
                    self.assertNotEqual(result.status_code, 200)

    def test_mcp_singleton_admin_no_delete_and_only_owner_edit(self):
        url = reverse("admin:billing_ownermcpsettings_change", args=[1])
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("admin:billing_ownermcpsettings_delete", args=[1])).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse("admin:billing_ownermcpsettings_add")).status_code,
            403,
        )
        self.client.force_login(self.staff)
        self.assertNotEqual(self.client.get(url).status_code, 200)

    def test_owner_switches_persist_and_change_real_tool_policy(self):
        policy = self.mcp_settings
        self.assertTrue(policy.allows("wb_products"))
        policy.wb_products_enabled = False
        policy.max_events = 3
        policy.save(update_fields=["wb_products_enabled", "max_events"])
        policy = OwnerMCPSettings.runtime()
        self.assertFalse(policy.allows("wb_products"))
        self.assertTrue(policy.allows("wb_events"))
        self.assertEqual(policy.max_events, 3)
        specs = definitions("wb", policy=policy)
        names = [tool["function"]["name"] for tool in specs]
        self.assertNotIn("wb_products", names)
        events = next(tool for tool in specs if tool["function"]["name"] == "wb_events")
        self.assertEqual(events["function"]["parameters"]["properties"]["limit"]["maximum"], 3)

    def test_policy_enforced_even_if_client_bypasses_model_specs(self):
        store = GuardAccount.objects.create(user=self.seller, name="WB seller", marketplace="wb")
        AccessGrant.objects.create(
            user=self.seller, source="admin", source_ref="owner-mcp-test",
            plan="standard", starts_at=timezone.now()-timedelta(minutes=1),
            ends_at=timezone.now()+timedelta(hours=1),
        )
        claims = {"user_id": self.seller.pk, "store_id": store.pk}
        self.mcp_settings.wb_products_enabled = False
        self.mcp_settings.max_events = 3
        self.mcp_settings.save()
        with patch("nosale_mcp.client.issue_store_token") as mint:
            with self.assertRaises(MCPToolUnavailable):
                call_owned_tool(self.seller, store, "wb_products", {"limit": 1})
            mint.assert_not_called()
        with self.assertRaises(PermissionError):
            _read_allowed(claims, "wb_products", lambda _: {"data": "unauthorized"})
        with self.assertRaises(PermissionError):
            _read_allowed(claims, "wb_events", lambda _, **k: {"data": k}, limit=4)
        result = _read_allowed(claims, "wb_events", lambda _, **k: {"data": k}, limit=3)
        self.assertEqual(result, {"data": {"limit": 3}})
        self.mcp_settings.enabled = False
        self.mcp_settings.save(update_fields=["enabled"])
        with self.assertRaises(PermissionError):
            _read_allowed(claims, "wb_events", lambda _: None)
        self.assertEqual(definitions("wb", policy=OwnerMCPSettings.runtime()), [])
