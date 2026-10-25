"""NoSale WB promotion audit: bounded read-only seller evidence."""
import base64
import io
import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant
from .models import GuardAccount, PromotionSnapshot
from .promotions import inspect_promotions
from .wb import WBApiError, WBClient


def token(kind=3, scopes=(1 << 3)):
    payload = base64.urlsafe_b64encode(
        json.dumps({"acc": kind, "s": scopes}).encode()
    ).decode().rstrip("=")
    return "eyJhbGciOiJIUzI1NiJ9." + payload + ".testsig"


def promo_list(*rows):
    return {"data": {"promotions": list(rows)}}


def participating(*goods):
    return {"data": {"nomenclatures": list(goods)}}


class PromotionAuditLogicTests(SimpleTestCase):
    def test_regular_price_drop_is_a_warning_not_proven_profit_or_funding(self):
        wb = Mock()
        wb.get_promotions_calendar.return_value = promo_list(
            {"id": 91, "type": "regular", "name": "Sale", "startDateTime": "2026-10-26T10:00:00Z"},
            {"id": 92, "type": "auto", "name": "Auto sale"},
        )
        wb.get_promotion_participants.return_value = participating(
            {"nmID": 123, "price": 1000, "planPrice": 800},
            {"nmID": 124, "price": 2000, "planPrice": 2000},
            {"nmID": 125, "price": 1500, "planPrice": None},
        )
        result = inspect_promotions(wb, token_type="service")
        self.assertTrue(result["partial"])
        self.assertEqual(result["promotions"][0]["risk"], "price_drop")
        self.assertEqual(result["promotions"][0]["funding"], "unknown")
        self.assertEqual(result["promotions"][0]["participation"]["price_decreases"], 1)
        self.assertEqual(result["promotions"][1]["risk"], "unknown")
        self.assertIsNone(result["promotions"][1]["participation"])
        wb.get_promotion_participants.assert_called_once_with(91, limit=100)

    def test_basic_token_only_gets_one_calendar_request(self):
        wb = Mock()
        wb.get_promotions_calendar.return_value = promo_list(
            {"id": 1, "type": "regular"}, {"id": 2, "type": "auto"},
        )
        result = inspect_promotions(wb, token_type="base")
        self.assertTrue(result["partial"])
        self.assertEqual(result["inspection_count"], 0)
        wb.get_promotions_calendar.assert_called_once()
        wb.get_promotion_participants.assert_not_called()

    def test_neutral_sample_is_not_claimed_profitable(self):
        wb = Mock()
        wb.get_promotions_calendar.return_value = promo_list({"id": 3, "type": "regular"})
        wb.get_promotion_participants.return_value = participating(
            {"nmID": 3, "price": 100, "planPrice": 100},
        )
        result = inspect_promotions(wb, token_type="personal")
        self.assertEqual(result["promotions"][0]["risk"], "no_drop_observed")
        self.assertEqual(result["promotions"][0]["funding"], "unknown")
        self.assertNotIn("прибыльна", result["promotions"][0]["explanation"])

    def test_bounded_api_calls_and_partial_pages(self):
        wb = Mock()
        wb.get_promotions_calendar.return_value = promo_list(
            *[{"id": i, "type": "regular"} for i in range(1, 45)],
        )
        wb.get_promotion_participants.return_value = participating()
        result = inspect_promotions(wb, token_type="service")
        self.assertTrue(result["partial"])
        self.assertEqual(len(result["promotions"]), 40)
        self.assertEqual(wb.get_promotion_participants.call_count, 3)

    def test_invalid_or_unsafe_price_does_not_become_zero(self):
        wb = Mock()
        wb.get_promotions_calendar.return_value = promo_list({"id": 50, "type": "regular"})
        wb.get_promotion_participants.return_value = participating(
            {"nmID": 123, "price": "not-a-number", "planPrice": 0},
            {"nmID": 124, "price": 0, "planPrice": 0},
        )
        result = inspect_promotions(wb, token_type="personal")
        self.assertEqual(result["promotions"][0]["risk"], "unknown")
        self.assertEqual(result["promotions"][0]["participation"]["missing_prices"], 2)


@override_settings(SAAS_WB_READ_ONLY=True, SAAS_WB_NETWORK_ENABLED=True)
class PromotionTransportTests(SimpleTestCase):
    def test_only_official_get_methods_are_used_with_fixed_host(self):
        wb = WBClient("opaque-fixture-only")
        with patch("guard.wb.urlopen", side_effect=[
            io.BytesIO(b'{"data":{"promotions":[]}}'),
            io.BytesIO(b'{"data":{"nomenclatures":[]}}'),
        ]) as opened:
            wb.get_promotions_calendar("2026-10-24T00:00:00Z", "2026-11-23T00:00:00Z")
            wb.get_promotion_participants(71)
        self.assertEqual(opened.call_count, 2)
        for call in opened.call_args_list:
            req = call.args[0]
            self.assertEqual(req.get_method(), "GET")
            self.assertTrue(req.full_url.startswith("https://dp-calendar-api.wildberries.ru/"))
        with patch("guard.wb.urlopen") as opened:
            with self.assertRaises(WBApiError):
                wb._request_to(wb.PROMOTIONS_BASE_URL, "POST", "/api/v1/calendar/promotions/upload", body={})
            opened.assert_not_called()


@override_settings(SAAS_WB_NETWORK_ENABLED=True, SAAS_PIN_AUTH_ENABLED=False,
                   SAAS_ACCESS_ENFORCED=True, SAAS_WB_READ_ONLY=True)
class PromotionPortalTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user("seller-promo@example.test", "seller-promo@example.test", "pass-strong-012")
        self.other = User.objects.create_user("other-promo@example.test", "other-promo@example.test", "pass-strong-012")
        self.account = GuardAccount.objects.create(
            user=self.seller, name="Мой WB", token_ciphertext="encrypted-fixture",
        )
        self.other_store = GuardAccount.objects.create(
            user=self.other, name="Чужой WB", token_ciphertext="not-ours",
        )
        AccessGrant.objects.create(
            user=self.seller, source="admin", source_ref="promo-test-grant",
            plan="standard", starts_at=timezone.now()-timedelta(minutes=1),
            ends_at=timezone.now()+timedelta(days=1),
        )
        self.url = reverse("promotion_audit")
        self.client.force_login(self.seller)

    def test_get_never_talks_to_wb_and_is_tenant_scoped(self):
        PromotionSnapshot.objects.create(account=self.other_store, promotions=[{"id": 999, "name": "Только чужой магазин"}])
        with patch("guard.promotions_views.WBClient") as client:
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "Только чужой магазин")
            self.assertContains(response, "Мой WB")
            client.assert_not_called()
        self.assertEqual(PromotionSnapshot.objects.filter(account=self.account).count(), 0)

    def test_refresh_is_readonly_stores_evidence_and_throttles(self):
        with patch("guard.promotions_views.decrypt_token", return_value=token()):
            with patch("guard.promotions_views.WBClient") as klass:
                klass.return_value.get_promotions_calendar.return_value = promo_list(
                    {"id": 71, "type": "auto", "name": "Автоматическая"},
                )
                self.assertRedirects(self.client.post(self.url, {"action": "refresh"}), self.url)
                self.assertRedirects(self.client.post(self.url, {"action": "refresh"}), self.url)
                klass.return_value.get_promotions_calendar.assert_called_once()
                klass.return_value.get_promotion_participants.assert_not_called()
        snap = PromotionSnapshot.objects.get(account=self.account)
        self.assertEqual(snap.promotions[0]["funding"], "unknown")
        self.assertEqual(snap.promotions[0]["type"], "auto")
        self.assertGreater(snap.next_refresh_at, timezone.now()+timedelta(minutes=14))
        self.assertContains(self.client.get(self.url), "Автоматическая")
        self.assertFalse(PromotionSnapshot.objects.filter(account=self.other_store).exists())

    def test_network_disabled_rejects_refresh_without_wb_calls(self):
        with override_settings(SAAS_WB_NETWORK_ENABLED=False):
            with patch("guard.promotions_views.WBClient") as client:
                self.assertRedirects(self.client.post(self.url, {"action": "refresh"}), self.url)
                self.assertEqual(PromotionSnapshot.objects.count(), 0)
                client.assert_not_called()

    def test_missing_scope_never_contacts_wb(self):
        with patch("guard.promotions_views.decrypt_token", return_value=token(scopes=(1 << 1))):
            with patch("guard.promotions_views.WBClient") as client:
                self.assertRedirects(self.client.post(self.url, {"action": "refresh"}), self.url)
                self.assertFalse(PromotionSnapshot.objects.filter(account=self.account).exists())
                client.assert_not_called()

    def test_429_preserves_previous_snapshot_and_cooldown(self):
        PromotionSnapshot.objects.create(account=self.account, promotions=[{"id": 51, "name": "Старая акция"}])
        with patch("guard.promotions_views.decrypt_token", return_value=token()):
            with patch("guard.promotions_views.WBClient") as klass:
                klass.return_value.get_promotions_calendar.side_effect = WBApiError("rate limit", status=429)
                self.assertRedirects(self.client.post(self.url, {"action": "refresh"}), self.url)
        row = PromotionSnapshot.objects.get(account=self.account)
        self.assertEqual(row.promotions[0]["id"], 51)
        self.assertIn("ограничил", row.last_error)
        self.assertIsNone(row.locked_until)
        self.assertGreater(row.next_refresh_at, timezone.now())

    def test_expired_grant_cannot_refresh_or_view_cached_seller_data(self):
        PromotionSnapshot.objects.create(account=self.account, promotions=[{"name": "Секретная акция"}])
        AccessGrant.objects.filter(user=self.seller).update(revoked_at=timezone.now())
        self.assertEqual(self.client.get(self.url).status_code, 403)
        with patch("guard.promotions_views.WBClient") as client:
            self.assertEqual(self.client.post(self.url, {"action": "refresh"}).status_code, 403)
            client.assert_not_called()

    def test_post_csrf_protected(self):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.seller)
        self.assertEqual(c.post(self.url, {"action": "refresh"}).status_code, 403)
