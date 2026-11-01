"""Strict evidence and UX contract for real WB campaigns."""
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant
from .models import GuardAccount, Product, PromotionSnapshot
from .promotion_presentation import _balanced_grid_spans, build_promotion_presentation
from .promotions import inspect_promotions, inspect_basic_promotion_step


def calendar(*campaigns):
    return {"data": {"promotions": list(campaigns)}}


def detail_rows(*campaigns):
    return {"data": {"promotions": list(campaigns)}}


def participants(*goods):
    return {"data": {"nomenclatures": list(goods)}}


class PromotionEvidenceTests(SimpleTestCase):
    def test_auto_campaign_has_aggregate_seller_data_but_no_sku_list(self):
        api = Mock()
        api.get_promotions_calendar.return_value = calendar(
            {"id": 501, "type": "auto", "name": "Авто", "startDateTime": "2026-11-01T00:00:00Z"},
        )
        api.get_promotion_details.return_value = detail_rows(
            {"id": 501, "type": "auto", "inPromoActionTotal": 8,
             "notInPromoActionTotal": 41, "exceptionProductsCount": 1},
        )
        result = inspect_promotions(api, token_type="service")
        row = result["promotions"][0]
        self.assertEqual(row["seller_counts"]["inPromoActionTotal"], 8)
        self.assertEqual(row["seller_counts"]["exceptionProductsCount"], 1)
        self.assertIsNone(row["participation"])
        self.assertEqual(row["risk"], "unknown")
        self.assertEqual(row["funding"], "unknown")
        self.assertEqual(result["details_requests"], 1)
        api.get_promotion_participants.assert_not_called()
        self.assertLessEqual(
            api.get_promotions_calendar.call_count
            + api.get_promotion_details.call_count
            + api.get_promotion_participants.call_count, 5,
        )

    def test_regular_participation_is_only_credited_to_matching_catalog_ids(self):
        api = Mock()
        api.get_promotions_calendar.return_value = calendar(
            {"id": 55, "type": "regular", "name": "WB обычная"},
        )
        api.get_promotion_details.return_value = detail_rows(
            {"id": 55, "inPromoActionTotal": 3},
        )
        api.get_promotion_participants.return_value = participants(
            {"nmID": 111, "price": 900, "planPrice": 750},
            {"nmID": 999999, "price": 500, "planPrice": 100},
        )
        own = {
            111: SimpleNamespace(title="Наш крем", vendor_code="A-111",
                                 current_discount=12, allowed_discount=15),
        }
        row = inspect_promotions(api, token_type="service", store_products=own)["promotions"][0]
        self.assertEqual(row["risk"], "price_drop")
        self.assertEqual(row["participation"]["matched_count"], 1)
        self.assertEqual(row["participation"]["matched_decreases"], 1)
        self.assertEqual(row["participation"]["price_decreases"], 2)
        self.assertEqual(len(row["participation"]["matched"]), 1)
        self.assertEqual(row["participation"]["matched"][0]["nm_id"], 111)
        self.assertEqual(row["participation"]["matched"][0]["planned"], "750.00")
        self.assertEqual(row["funding"], "unknown")

    def test_unmatched_decrease_never_becomes_a_seller_risk(self):
        api = Mock()
        api.get_promotions_calendar.return_value = calendar({"id": 60, "type": "regular"})
        api.get_promotion_details.return_value = detail_rows({"id": 60})
        api.get_promotion_participants.return_value = participants(
            {"nmID": 8888, "price": 500, "planPrice": 100},
        )
        row = inspect_promotions(
            api, token_type="service",
            store_products={111: SimpleNamespace(
                title="Own", vendor_code="A", current_discount=10, allowed_discount=15,
            )},
        )["promotions"][0]
        self.assertEqual(row["risk"], "unknown")
        self.assertEqual(row["participation"]["matched_count"], 0)
        self.assertIn("не подтверждено", row["explanation"])

    def test_basic_token_progressively_checks_totals_then_one_regular_promotion(self):
        now = datetime(2026, 11, 1, 9, tzinfo=dt_timezone.utc)
        api = Mock()
        api.get_promotions_calendar.return_value = calendar(
            {"id": 501, "type": "auto", "name": "Auto", "startDateTime": "2026-11-01T00:00:00Z"},
            {"id": 502, "type": "regular", "name": "Regular", "startDateTime": "2026-11-01T00:00:00Z"},
        )
        api.get_promotion_details.return_value = detail_rows(
            {"id": 501, "inPromoActionTotal": 3},
            {"id": 502, "inPromoActionTotal": 1},
        )
        api.get_promotion_participants.return_value = participants(
            {"nmID": 111, "price": 1000, "planPrice": 800},
        )
        store = {111: SimpleNamespace(
            title="Наш товар", vendor_code="OWN", current_discount=10, allowed_discount=13,
        )}
        first = inspect_basic_promotion_step(
            api, cached_promotions=[], calendar_fetched_at=None,
            store_products=store, at=now,
        )
        self.assertEqual(first["phase"], "calendar")
        self.assertEqual(api.get_promotions_calendar.call_count, 1)
        api.reset_mock()
        second = inspect_basic_promotion_step(
            api, cached_promotions=first["promotions"], calendar_fetched_at=now,
            store_products=store, at=now+timedelta(minutes=70),
        )
        self.assertEqual(second["phase"], "details")
        self.assertEqual(second["promotions"][0]["seller_counts"]["inPromoActionTotal"], 3)
        self.assertEqual(second["promotions"][1]["seller_counts"]["inPromoActionTotal"], 1)
        self.assertEqual(api.get_promotion_details.call_count, 1)
        api.get_promotions_calendar.assert_not_called()
        api.get_promotion_participants.assert_not_called()
        api.reset_mock()
        third = inspect_basic_promotion_step(
            api, cached_promotions=second["promotions"], calendar_fetched_at=now,
            store_products=store, at=now+timedelta(minutes=140),
        )
        self.assertEqual(third["phase"], "participants")
        self.assertEqual(third["promotions"][1]["participation"]["matched_count"], 1)
        self.assertEqual(third["promotions"][1]["risk"], "price_drop")
        api.get_promotion_details.assert_not_called()
        api.get_promotions_calendar.assert_not_called()
        self.assertEqual(api.get_promotion_participants.call_count, 1)

    def test_base_calendar_refresh_keeps_only_recent_matching_evidence(self):
        now = datetime(2026, 11, 1, 12, tzinfo=dt_timezone.utc)
        api = Mock()
        api.get_promotions_calendar.return_value = calendar(
            {"id": 42, "type": "auto", "name": "Unchanged",
             "startDateTime": "2026-10-30T00:00:00Z", "endDateTime": "2026-11-14T00:00:00Z"},
        )
        recent = [{
            "id": 42, "type": "auto", "starts_at": "2026-10-30T00:00:00Z",
            "ends_at": "2026-11-14T00:00:00Z",
            "seller_counts": {"inPromoActionTotal": 4},
            "details_checked_at": (now-timedelta(hours=2)).isoformat(),
        }]
        refreshed = inspect_basic_promotion_step(
            api, cached_promotions=recent,
            calendar_fetched_at=now-timedelta(hours=25), at=now,
        )
        self.assertEqual(refreshed["phase"], "calendar")
        self.assertEqual(refreshed["promotions"][0]["seller_counts"]["inPromoActionTotal"], 4)
        self.assertEqual(api.get_promotions_calendar.call_count, 1)
        api.reset_mock()
        stale = [dict(recent[0], details_checked_at=(now-timedelta(hours=27)).isoformat())]
        result = inspect_basic_promotion_step(
            api, cached_promotions=stale,
            calendar_fetched_at=now-timedelta(hours=25), at=now,
        )
        self.assertIsNone(result["promotions"][0]["seller_counts"])
        api.get_promotion_details.assert_not_called()

    def test_basic_and_unknown_token_read_calendar_once_only(self):
        for token_type in ("base", None, "unknown"):
            with self.subTest(token_type=token_type):
                api = Mock()
                api.get_promotions_calendar.return_value = calendar(
                    {"id": 12, "type": "regular"},
                    {"id": 13, "type": "auto"},
                )
                evidence = inspect_promotions(api, token_type=token_type)
                self.assertEqual(len(evidence["promotions"]), 2)
                self.assertEqual(evidence["inspection_count"], 0)
                self.assertEqual(evidence["details_requests"], 0)
                api.get_promotion_details.assert_not_called()
                api.get_promotion_participants.assert_not_called()

    def test_wb_429_on_details_preserves_calendar_without_other_requests(self):
        from .wb import WBApiError
        api = Mock()
        api.get_promotions_calendar.return_value = calendar({"id": 13, "type": "regular"})
        api.get_promotion_details.side_effect = WBApiError("rate", status=429)
        result = inspect_promotions(api, token_type="service")
        self.assertEqual(len(result["promotions"]), 1)
        self.assertTrue(result["partial"])
        api.get_promotion_participants.assert_not_called()

    def test_filtering_status_and_unknown_does_not_invent_participation(self):
        now = datetime(2026, 11, 1, 12, tzinfo=dt_timezone.utc)
        rows = [
            {"id": 1, "type": "auto", "name": "Running", "starts_at": "2026-10-29T09:00:00Z",
             "ends_at": "2026-11-20T00:00:00Z", "seller_counts": {"inPromoActionTotal": 5}},
            {"id": 2, "type": "regular", "name": "Upcoming",
             "starts_at": "2026-11-07T09:00:00Z", "ends_at": "2026-11-20T00:00:00Z",
             "participation": {"matched": [{"nm_id": 123, "decrease": True}]}},
            {"id": 3, "type": "auto", "name": "Finished",
             "starts_at": "2026-10-01T09:00:00Z", "ends_at": "2026-10-10T00:00:00Z"},
            {"id": 4, "type": "regular", "name": "No dates"},
        ]
        all_rows = build_promotion_presentation(rows, at=now)
        self.assertEqual(all_rows["promotion_counts"]["active"], 1)
        self.assertEqual(all_rows["promotion_counts"]["upcoming"], 1)
        self.assertEqual(all_rows["promotion_counts"]["finished"], 1)
        self.assertEqual(all_rows["promotion_counts"]["price_drop"], 1)
        self.assertEqual(all_rows["promotion_counts"]["participating"], 2)
        self.assertEqual(all_rows["promotion_counts"]["unknown"], 2)
        self.assertEqual(
            [row["id"] for row in build_promotion_presentation(
                rows, at=now, selected_filter="price_drop",
            )["promotion_cards"]], [2],
        )
        self.assertEqual(
            [row["id"] for row in build_promotion_presentation(
                rows, at=now, selected_filter="unknown",
            )["promotion_cards"]], [3, 4],
        )
        self.assertEqual(
            build_promotion_presentation(rows, at=now, selected_filter="../../../")["promotion_filter"], "all",
        )

    def test_zero_aggregate_is_confirmed_zero_not_unknown(self):
        item = build_promotion_presentation([
            {"id": 4, "seller_counts": {"inPromoActionTotal": 0}},
        ])["promotion_cards"][0]
        self.assertTrue(item["aggregate_known"])
        self.assertEqual(item["aggregate_count"], 0)
        self.assertFalse(item["is_unverified"])
        self.assertFalse(item["has_participation"])

    def test_twelve_column_grid_has_no_orphan_last_row(self):
        expected = {
            0: [], 1: [12], 2: [6, 6], 3: [4, 4, 4],
            4: [3] * 4, 5: [4] * 3 + [6] * 2,
            6: [4] * 6, 7: [3] * 4 + [4] * 3,
            8: [3] * 8, 9: [4] * 9,
            10: [3] * 4 + [4] * 6,
            11: [3] * 8 + [4] * 3,
            12: [3] * 12,
        }
        for number, spans in expected.items():
            with self.subTest(number=number):
                self.assertEqual(_balanced_grid_spans(number), spans)
                presentation = build_promotion_presentation(
                    [{"id": idx, "name": f"Campaign {idx}", "type": "auto"}
                     for idx in range(number)],
                )
                self.assertEqual(
                    [card["grid_span"] for card in presentation["promotion_cards"]],
                    spans,
                )

    def test_filter_rebalances_only_visible_cards(self):
        rows = [
            {"id": idx, "type": "auto" if idx < 6 else "regular"}
            for idx in range(8)
        ]
        all_cards = build_promotion_presentation(rows)["promotion_cards"]
        self.assertEqual([x["grid_span"] for x in all_cards], [3] * 8)
        filtered = build_promotion_presentation(rows, selected_filter="auto")
        self.assertEqual([x["grid_span"] for x in filtered["promotion_cards"]], [4] * 6)

    def test_bounded_details_and_product_lists(self):
        api = Mock()
        api.get_promotions_calendar.return_value = calendar(
            *[{"id": x, "type": "regular"} for x in range(1, 41)]
        )
        api.get_promotion_details.return_value = detail_rows()
        api.get_promotion_participants.return_value = participants()
        evidence = inspect_promotions(api, token_type="service")
        self.assertTrue(evidence["partial"])
        self.assertEqual(api.get_promotions_calendar.call_count, 1)
        self.assertEqual(api.get_promotion_details.call_count, 1)
        self.assertEqual(api.get_promotion_participants.call_count, 3)
        self.assertLessEqual(len(evidence["promotions"]), 40)


@override_settings(SAAS_WB_NETWORK_ENABLED=False, SAAS_PIN_AUTH_ENABLED=False,
                   SAAS_ACCESS_ENFORCED=True, SAAS_WB_READ_ONLY=True)
class PromotionReadOnlyPortalTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.seller = User.objects.create_user(
            username="seller-promorisk@example.test", password="safe-fixture",
        )
        self.other_user = User.objects.create_user(
            username="another-promorisk@example.test", password="safe-fixture",
        )
        self.shop = GuardAccount.objects.create(user=self.seller, name="Current WB",
                                               default_allowed_discount=13)
        self.other = GuardAccount.objects.create(user=self.other_user, name="Not Yours")
        AccessGrant.objects.create(
            user=self.seller, source="admin", source_ref="promo-risk-ui",
            plan="standard", starts_at=timezone.now()-timedelta(hours=1),
            ends_at=timezone.now()+timedelta(days=1),
        )
        Product.objects.create(account=self.shop, nm_id=555, title="Наш крем",
                               price="1000.00", discounted_price="850.00",
                               current_discount=15, allowed_discount_override=13)
        Product.objects.create(account=self.other, nm_id=999, title="Чужой товар",
                               current_discount=99)
        self.client.force_login(self.seller)

    def test_campaign_filters_are_store_scoped_and_snapshot_only(self):
        now = timezone.now()
        PromotionSnapshot.objects.create(
            account=self.shop, fetched_at=now,
            promotions=[{"id": 71, "type": "regular", "name": "Моя акция",
                         "starts_at": (now-timedelta(days=1)).isoformat(),
                         "ends_at": (now+timedelta(days=1)).isoformat(),
                         "seller_counts": {"inPromoActionTotal": 1},
                         "participation": {"matched": [
                             {"nm_id": 555, "title": "Наш крем",
                              "current": "900.00", "planned": "750.00",
                              "decrease": True, "current_discount": 15, "allowed_discount": 13,
                             }
                         ], "matched_count": 1, "matched_decreases": 1}}],
        )
        PromotionSnapshot.objects.create(
            account=self.other, fetched_at=now,
            promotions=[{"id": 777, "name": "Секрет чужой акции", "type": "auto"}],
        )
        with patch("guard.promotions_views.WBClient") as wb:
            response = self.client.get(reverse("promotion_audit") + "?filter=price_drop")
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Наш крем")
            self.assertContains(response, "750.00")
            self.assertContains(response, "Плановая цена ниже")
            self.assertContains(response, "товаров продавца")
            self.assertNotContains(response, "Секрет чужой акции")
            self.assertNotContains(response, "Чужой товар")
            self.assertEqual(response.context["catalog_total"], 1)
            self.assertEqual(response.context["catalog_over_limit"], 1)
            wb.assert_not_called()
        empty = self.client.get(reverse("promotion_audit") + "?filter=finished")
        self.assertContains(empty, "Нет акций в выбранной категории")
        main = empty.content.decode().split("<main", 1)[1].split("</main>", 1)[0]
        self.assertNotIn("<details", main)

    def test_six_campaigns_render_as_two_full_three_card_rows(self):
        PromotionSnapshot.objects.create(
            account=self.shop,
            fetched_at=timezone.now(),
            promotions=[
                {"id": idx + 100, "name": f"Акция {idx + 1}", "type": "auto"}
                for idx in range(6)
            ],
        )
        response = self.client.get(reverse("promotion_audit"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'promo-audit-span-4', count=6)
        self.assertEqual(
            [item["grid_span"] for item in response.context["promotion_cards"]],
            [4] * 6,
        )

    def test_legacy_calendar_is_readable_without_mutating_snapshot(self):
        snap = PromotionSnapshot.objects.create(
            account=self.shop, promotions=[{
                "id": 11, "name": "WB календарь", "type": "auto",
                "starts_at": "2026-11-01T00:00:00Z",
                "ends_at": "2026-11-25T00:00:00Z",
                "funding": "unknown", "risk": "unknown", "participation": None,
            }], fetched_at=timezone.now(),
        )
        before = snap.promotions
        response = self.client.get(reverse("promotion_audit"))
        self.assertContains(response, "WB календарь")
        self.assertContains(response, "не подтверждено")
        self.assertContains(response, "неизвестно")
        self.assertNotContains(response, "Выгодно")
        snap.refresh_from_db()
        self.assertEqual(snap.promotions, before)
