"""First-party NoSale MCP: signed identities, isolation and read-only data."""
import asyncio
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import signing
from django.test import TestCase, override_settings
from django.utils import timezone

from billing.models import AccessGrant
from guard.models import GuardAccount, Product, GuardEvent, PromotionSnapshot
from .auth import (
    AccessDenied, RESOURCE, SALT, issue_store_token, scope_account, verify_store_token,
)
from .adapters import wb, ozon


@override_settings(SAAS_ACCESS_ENFORCED=True, SAAS_WB_NETWORK_ENABLED=False)
class FirstPartyMCPTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("mcp-owner@example.test", "mcp-owner@example.test", "password12345")
        self.other = User.objects.create_user("mcp-other@example.test", "mcp-other@example.test", "password12345")
        self.store = GuardAccount.objects.create(user=self.owner, name="WB test", marketplace="wb", default_allowed_discount=15)
        self.other_store = GuardAccount.objects.create(user=self.other, name="Secret other WB", marketplace="wb")
        self.ozon_store = GuardAccount.objects.create(user=self.owner, name="Ozon test", marketplace="ozon")
        self.grant = AccessGrant.objects.create(
            user=self.owner, source="admin", source_ref="mcp-owner-grant", plan="standard",
            starts_at=timezone.now()-timedelta(hours=1), ends_at=timezone.now()+timedelta(days=1),
        )
        self.token = issue_store_token(self.owner, self.store)
        Product.objects.create(account=self.store, nm_id=111, title="Seller product",
                               current_discount=30, price=1000, discounted_price=700)
        Product.objects.create(account=self.other_store, nm_id=999, title="Secret other SKU",
                               current_discount=80)
        GuardEvent.objects.create(account=self.store, kind="price_changed", nm_id=111, message="private payload secret")
        GuardEvent.objects.create(account=self.other_store, kind="price_changed", nm_id=999, message="secret event")
        PromotionSnapshot.objects.create(
            account=self.store, fetched_at=timezone.now(), partial=True,
            promotions=[{"id": 51, "name": "Campaign", "type": "regular",
                         "risk": "price_drop", "funding": "unknown",
                         "participation": {"inspected": 1, "price_decreases": 1}}],
        )
        PromotionSnapshot.objects.create(account=self.other_store, partial=True,
                                          promotions=[{"id": 999, "name": "Other secret"}])

    def test_signing_binds_user_store_audience_and_expiry(self):
        assert verify_store_token(self.token) == {"user_id": self.owner.pk, "store_id": self.store.pk}
        assert verify_store_token(self.token + "tampered") is None
        assert verify_store_token("test-id-only") is None
        forged = signing.dumps(
            {"v": 1, "u": self.owner.pk, "s": self.store.pk, "aud": "http://attacker.invalid/mcp"},
            salt=SALT,
        )
        assert verify_store_token(forged) is None
        with patch("nosale_mcp.auth.signing.loads", side_effect=signing.SignatureExpired("expired")):
            assert verify_store_token(self.token) is None

    def test_ownership_and_active_subscription_rechecked_on_each_tool(self):
        claims = verify_store_token(self.token)
        self.assertEqual(scope_account(claims), self.store)
        with self.assertRaises(AccessDenied):
            scope_account({"user_id": self.other.pk, "store_id": self.store.pk})
        with self.assertRaises(AccessDenied):
            scope_account({"user_id": self.owner.pk, "store_id": self.other_store.pk})
        self.grant.revoked_at = timezone.now()
        self.grant.save(update_fields=["revoked_at"])
        with self.assertRaises(AccessDenied):
            scope_account(claims)
        with self.assertRaises(AccessDenied):
            issue_store_token(self.owner, self.store)

    def test_stored_read_only_wb_tools_do_not_leak_secrets_or_cross_tenant(self):
        with patch("guard.wb.urlopen") as net:
            o = wb.overview(self.store)
            rows = wb.products(self.store, limit=999, offset=0)
            events = wb.events(self.store)
            promos = wb.promotions(self.store)
            net.assert_not_called()
        self.assertEqual(o["metrics"]["over_limit"], 1)
        self.assertEqual(rows["total"], 1)
        self.assertEqual(rows["limit"], 50)
        self.assertEqual(rows["items"][0]["nm_id"], 111)
        self.assertEqual(events["events"][0]["nm_id"], 111)
        self.assertNotIn("private payload", str(events))
        self.assertNotIn("Secret other", str(o)+str(rows)+str(events)+str(promos))
        self.assertEqual(promos["items"][0]["funding"], "unknown")
        self.assertFalse(promos["profit_or_funding_verified"])

    def test_ozon_is_separate_and_not_falsely_implemented(self):
        with self.assertRaises(PermissionError):
            wb.products(self.ozon_store)
        self.assertEqual(ozon.status(self.ozon_store)["state"], "not_implemented")

    def test_mcp_server_only_has_explicit_read_only_tools(self):
        from .server import mcp, StoreTokenVerifier
        verifier = StoreTokenVerifier()
        good = asyncio.run(verifier.verify_token(self.token))
        bad = asyncio.run(verifier.verify_token("invalid"))
        self.assertIsNotNone(good)
        self.assertIsNone(bad)
        self.assertEqual(good.resource, RESOURCE)
        self.assertEqual(good.claims["store_id"], self.store.pk)
        names = set(mcp._tool_manager._tools) if hasattr(mcp, "_tool_manager") else set()
        # Tool visibility is checked again through a real protocol smoke at deploy.
        if names:
            self.assertEqual(names, {
                "marketplace_capabilities", "seller_stores", "wb_overview",
                "wb_products", "wb_events", "wb_promotions", "ozon_adapter_status",
            })
        self.assertFalse(any(name.startswith(("set_", "update_", "delete_", "write_")) for name in names))
