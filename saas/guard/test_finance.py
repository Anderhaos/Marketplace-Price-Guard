"""Opt-in, tenant-scoped WB Finance read-only integration contract."""
import base64
import io
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant
from guard.finance import (
    FinanceUnavailable, fetch_reports_list, normalize_reports,
    require_finance_service_token, REPORTS_PATH, BASE_URL
)
from guard.models import FinanceSnapshot, FinanceRateGate, GuardAccount


def finance_token(*, kind=4, scopes=1 << 13):
    encoded = base64.urlsafe_b64encode(json.dumps({"acc": kind, "s": scopes}).encode()).decode().rstrip("=")
    return "header." + encoded + ".syntheticlongenoughsignaturefortests"


class FinanceContractTests(SimpleTestCase):
    def test_only_separate_service_token_in_finance_category(self):
        self.assertEqual(require_finance_service_token(finance_token()), finance_token())
        for token in (
            finance_token(kind=3), finance_token(kind=1),
            finance_token(kind=2), finance_token(scopes=1 << 3),
            "a bad fake", "x" * 4097,
        ):
            with self.subTest(token_hint=token[:12]), self.assertRaises(FinanceUnavailable):
                require_finance_service_token(token)

    def test_financial_summaries_never_infer_profits_or_funding(self):
        rows = normalize_reports([{
            "reportId": 123, "dateFrom": "2026-10-01", "dateTo": "2026-10-07",
            "currency": "RUB", "forPaySum": "127.9",
            "retailAmountSum": "525.71", "sellerFinanceName": "SENSITIVE CORP",
            "bankPaymentSum": "124.00", "sellerPromo": 99,
            "privateBuyerPhone": "should_not_survive",
        }])
        self.assertEqual(rows, [{
            "report_id": 123, "start": "2026-10-01", "end": "2026-10-07",
            "currency": "RUB", "for_pay": "127.90", "retail_amount": "525.71",
            "source": "WB Finance realization reports (summary)",
            "funding": "unknown", "profit_verified": False,
        }])
        self.assertNotIn("SENSITIVE CORP", json.dumps(rows))
        self.assertNotIn("should_not_survive", json.dumps(rows))

    def test_invalid_monetary_data_is_unknown_not_zero(self):
        rows = normalize_reports([{
            "reportId": 999, "dateFrom": "2026-10-01", "dateTo": "2026-10-07",
            "currency": "RUB", "forPaySum": "NaN", "retailAmountSum": [],
        }])
        self.assertIsNone(rows[0]["for_pay"])
        self.assertIsNone(rows[0]["retail_amount"])
        for sample in ([{"reportId": True, "dateFrom": "2026-10-01", "dateTo": "2026-10-07", "currency": "RUB"}],
                       [{"reportId": 1, "dateFrom": "2026-11-07", "dateTo": "2026-10-01", "currency": "RUB"}],
                       [42], [], [None]):
            if sample in ([],):
                self.assertEqual(normalize_reports(sample), [])
            else:
                with self.assertRaises(FinanceUnavailable):
                    normalize_reports(sample)

    @override_settings(SAAS_WB_NETWORK_ENABLED=False, NOSALE_WB_FINANCE_ENABLED=True)
    def test_global_network_off_blocks_finance_before_http(self):
        with patch("guard.finance.build_opener") as opener:
            with self.assertRaises(FinanceUnavailable):
                fetch_reports_list(finance_token(), "2026-10-01", "2026-10-20")
            opener.assert_not_called()

    @override_settings(SAAS_WB_NETWORK_ENABLED=True, NOSALE_WB_FINANCE_ENABLED=False)
    def test_independent_finance_switch_blocks_http(self):
        with patch("guard.finance.build_opener") as opener:
            with self.assertRaises(FinanceUnavailable):
                fetch_reports_list(finance_token(), "2026-10-01", "2026-10-20")
            opener.assert_not_called()

    @override_settings(SAAS_WB_NETWORK_ENABLED=True, NOSALE_WB_FINANCE_ENABLED=True)
    def test_fixed_post_read_to_official_finance_origin_with_bounded_window(self):
        class FakeResponse:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, n):
                return b'[{"reportId":123,"dateFrom":"2026-10-01","dateTo":"2026-10-07","currency":"RUB","forPaySum":"0.00"}]'
        class Opener:
            def open(self, request, timeout):
                self.request = request
                return FakeResponse()
        opener = Opener()
        with patch("guard.finance.build_opener", return_value=opener):
            rows, partial = fetch_reports_list(finance_token(), "2026-10-01", "2026-10-20")
        self.assertFalse(partial)
        self.assertEqual(rows[0]["report_id"], 123)
        self.assertEqual(opener.request.full_url, BASE_URL + REPORTS_PATH)
        self.assertEqual(opener.request.get_method(), "POST")
        self.assertEqual(opener.request.get_header("Authorization"), finance_token())
        sent = json.loads(opener.request.data)
        self.assertEqual(sent["limit"], 50)
        self.assertEqual(sent["period"], "weekly")
        self.assertNotIn("token", sent)
        with self.assertRaises(FinanceUnavailable):
            fetch_reports_list(finance_token(), "2026-01-01", "2026-10-25")


@override_settings(SAAS_WB_NETWORK_ENABLED=True,
                   NOSALE_WB_FINANCE_ENABLED=True,
                   SAAS_PIN_AUTH_ENABLED=False,
                   SAAS_ACCESS_ENFORCED=True)
class FinanceSellerPortalTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user("finance-own@example.test", password="test-only")
        self.other = User.objects.create_user("finance-other@example.test", password="test-only")
        self.store = GuardAccount.objects.create(user=self.seller, name="Own finance shop")
        self.foreign = GuardAccount.objects.create(user=self.other, name="FOREIGN-SECRET-SHOP")
        AccessGrant.objects.create(
            user=self.seller, source="admin", source_ref="finance-test",
            plan="standard", starts_at=timezone.now()-timedelta(minutes=1),
            ends_at=timezone.now()+timedelta(days=1),
        )
        self.client.force_login(self.seller)

    def test_get_never_calls_wb_or_reveals_foreign_financial_records(self):
        FinanceSnapshot.objects.create(
            account=self.foreign, reports=[{"report_id": 8765, "start": "2026-10-01"}]
        )
        with patch("guard.finance_views.fetch_reports_list") as fetch:
            page = self.client.get(reverse("finance_audit"))
            self.assertEqual(page.status_code, 200)
            self.assertContains(page, "Own finance shop")
            self.assertNotContains(page, "FOREIGN-SECRET-SHOP")
            self.assertNotContains(page, "8765")
            fetch.assert_not_called()

    def test_connect_explicit_consent_encrypt_refresh_throttle_and_revoke(self):
        tok = finance_token()
        self.client.post(reverse("finance_audit"), {
            "action": "connect", "finance_token": tok,
        })
        self.assertFalse(FinanceSnapshot.objects.filter(account=self.store).exists())
        connect = self.client.post(reverse("finance_audit"), {
            "action": "connect", "finance_consent": "yes", "finance_token": tok,
        })
        self.assertEqual(connect.status_code, 302)
        state = FinanceSnapshot.objects.get(account=self.store)
        self.assertNotIn(tok, state.encrypted_finance_token)
        self.assertEqual(state.token_hint, "сервисный · Финансы")
        self.assertGreaterEqual(FinanceSnapshot._meta.get_field("token_hint").max_length, len(state.token_hint))
        self.assertIsNotNone(state.consented_at)
        self.assertNotIn(tok, self.client.get(reverse("finance_audit")).content.decode())
        fake_rows = [{
            "report_id": 123, "start": "2026-10-01", "end": "2026-10-07",
            "currency": "RUB", "for_pay": "12.00", "retail_amount": "33.00",
            "source": "WB Finance realization reports (summary)",
            "funding": "unknown", "profit_verified": False,
        }]
        with patch("guard.finance_views.fetch_reports_list", return_value=(fake_rows, False)) as fetch:
            result = self.client.post(reverse("finance_audit"), {"action": "refresh"})
            self.assertEqual(result.status_code, 302)
            self.client.post(reverse("finance_audit"), {"action": "refresh"})
            fetch.assert_called_once()
        state.refresh_from_db()
        self.assertEqual(state.reports[0]["report_id"], 123)
        self.assertEqual(state.reports[0]["funding"], "unknown")
        self.assertIsNotNone(state.next_refresh_at)
        with patch("guard.finance_views.fetch_reports_list") as fetch:
            self.client.post(reverse("finance_audit"), {"action": "revoke"})
            self.assertFalse(FinanceSnapshot.objects.filter(account=self.store).exists())
            fetch.assert_not_called()

    def test_rotation_preserves_75_second_rate_limit(self):
        tok = finance_token()
        self.client.post(reverse("finance_audit"), {
            "action": "connect", "finance_consent": "yes", "finance_token": tok,
        })
        FinanceSnapshot.objects.filter(account=self.store).update(
            next_refresh_at=timezone.now() + timedelta(seconds=70),
        )
        rotated = finance_token(scopes=(1 << 13) | (1 << 3))
        self.client.post(reverse("finance_audit"), {
            "action": "connect", "finance_consent": "yes", "finance_token": rotated,
        })
        state = FinanceSnapshot.objects.get(account=self.store)
        self.assertGreater(state.next_refresh_at, timezone.now())
        with patch("guard.finance_views.fetch_reports_list") as fetch:
            self.client.post(reverse("finance_audit"), {"action": "refresh"})
            fetch.assert_not_called()

    def test_stale_refresh_does_not_republish_after_revocation_or_rotation(self):
        tok = finance_token()
        self.client.post(reverse("finance_audit"), {
            "action": "connect", "finance_consent": "yes", "finance_token": tok,
        })
        def revoke_during_request(*args):
            FinanceSnapshot.objects.filter(account=self.store).delete()
            return ([{"report_id": 42}], False)
        with patch("guard.finance_views.fetch_reports_list", side_effect=revoke_during_request):
            self.client.post(reverse("finance_audit"), {"action": "refresh"})
        self.assertFalse(FinanceSnapshot.objects.filter(account=self.store).exists())

    def test_revocation_does_not_reset_gate_after_reconnect(self):
        tok = finance_token()
        self.client.post(reverse("finance_audit"), {
            "action": "connect", "finance_consent": "yes", "finance_token": tok,
        })
        with patch("guard.finance_views.fetch_reports_list", return_value=([], False)) as fetch:
            self.client.post(reverse("finance_audit"), {"action": "refresh"})
            fetch.assert_called_once()
        self.assertTrue(FinanceRateGate.objects.filter(user=self.seller).exists())
        self.client.post(reverse("finance_audit"), {"action": "revoke"})
        self.assertFalse(FinanceSnapshot.objects.filter(account=self.store).exists())
        self.assertGreater(
            FinanceRateGate.objects.get(user=self.seller).next_allowed_at, timezone.now()
        )
        self.client.post(reverse("finance_audit"), {
            "action": "connect", "finance_consent": "yes", "finance_token": tok,
        })
        with patch("guard.finance_views.fetch_reports_list") as fetch:
            self.client.post(reverse("finance_audit"), {"action": "refresh"})
            fetch.assert_not_called()

    @override_settings(NOSALE_WB_FINANCE_ENABLED=False)
    def test_revoke_is_available_when_operator_disables_finance(self):
        FinanceSnapshot.objects.create(
            account=self.store, encrypted_finance_token="synthetic-cipher",
            reports=[{"report_id": 1}], consented_at=timezone.now(),
        )
        response = self.client.get(reverse("finance_audit"))
        self.assertContains(response, "Отключить и удалить финансовые сведения")
        self.assertNotContains(response, 'name="finance_token"')
        response = self.client.post(reverse("finance_audit"), {"action": "revoke"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(FinanceSnapshot.objects.filter(account=self.store).exists())

    def test_expired_subscription_can_delete_finance_without_viewing_data(self):
        FinanceSnapshot.objects.create(
            account=self.store, encrypted_finance_token="synthetic-cipher",
            reports=[{"report_id": 194, "for_pay": "4890.00"}],
            consented_at=timezone.now(),
        )
        AccessGrant.objects.filter(user=self.seller).update(
            ends_at=timezone.now() - timedelta(days=2)
        )
        with patch("guard.finance_views.fetch_reports_list") as fetch:
            page = self.client.get(reverse("finance_audit"))
            self.assertEqual(page.status_code, 200)
            self.assertContains(page, "Отключить и удалить финансовые сведения")
            self.assertNotContains(page, "4890.00")
            self.assertNotContains(page, "194")
            self.client.post(reverse("finance_audit"), {"action": "revoke"})
            fetch.assert_not_called()
        self.assertFalse(FinanceSnapshot.objects.filter(account=self.store).exists())

    @override_settings(NOSALE_WB_FINANCE_ENABLED=False)
    def test_disabled_finance_never_accepts_or_exposes_token(self):
        with patch("guard.finance_views.fetch_reports_list") as fetch:
            result = self.client.post(reverse("finance_audit"), {
                "action": "connect", "finance_consent": "yes",
                "finance_token": finance_token(),
            })
            self.assertEqual(result.status_code, 302)
            self.assertFalse(FinanceSnapshot.objects.exists())
            fetch.assert_not_called()
        page = self.client.get(reverse("finance_audit"))
        self.assertNotContains(page, 'name="finance_token"')
        self.assertContains(page, "Финансовое подключение ещё не открыто")
