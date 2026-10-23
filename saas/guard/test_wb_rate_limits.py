"""WB 429 cooldowns must survive workers, reloads and manual retries."""
import io
import json
from datetime import timedelta
from unittest.mock import patch
from urllib.error import HTTPError

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import GuardAccount
from .monitor import run_due_checks
from .test_onboarding_trial import token_for
from .wb import WBApiError, WBClient
from django.contrib.auth.models import User


@override_settings(SAAS_WB_READ_ONLY=True)
class BasicPriceApiLimitTests(SimpleTestCase):
    def test_short_last_page_avoids_one_extra_price_api_request(self):
        result = {"data": {"listGoods": [
            {"nmID": 42, "discount": 20, "sizes": []}
        ]}}
        wb = WBClient("fake")
        with patch("guard.wb.urlopen", return_value=io.BytesIO(json.dumps(result).encode())) as transport:
            goods = wb.get_products()
        self.assertEqual(len(goods), 1)
        transport.assert_called_once()

    def test_429_keeps_wb_retry_after_without_retrying_in_the_same_request(self):
        response = HTTPError(
            "https://discounts-prices-api.wildberries.ru/api/v2/list/goods/filter",
            429, "Too Many Requests", {"X-Ratelimit-Retry": "3600"},
            io.BytesIO(b'{"title":"Too Many Requests"}'),
        )
        with patch("guard.wb.urlopen", side_effect=response) as transport:
            with self.assertRaises(WBApiError) as ctx:
                WBClient("fake").get_products()
        self.assertEqual(ctx.exception.status, 429)
        self.assertEqual(ctx.exception.retry_after, 3600)
        transport.assert_called_once()


@override_settings(
    SAAS_PIN_AUTH_ENABLED=True, SAAS_ACCESS_ENFORCED=True,
    SAAS_WB_TOKEN_VALIDATION_ENABLED=True, SAAS_WB_NETWORK_ENABLED=True,
    SAAS_WB_READ_ONLY=True,
)
class BasicOnboardingCooldownTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="cooldown@example.test", email="cooldown@example.test",
            password="SafePassA1",
        )
        self.client.force_login(self.user)
        with patch("guard.integrations.WBClient"):
            response = self.client.post(
                reverse("connect_wb"), {"token": token_for(kind=1)}
            )
        self.assertRedirects(response, reverse("dashboard"))
        self.account = GuardAccount.objects.get(user=self.user)

    @patch("guard.monitor.WBClient")
    def test_429_schedules_background_retry_and_disables_manual_retry(self, wb_class):
        wb_class.return_value.get_product_cards.return_value = []
        wb_class.return_value.get_products.side_effect = WBApiError(
            "WB HTTP 429 at GET /api/v2/list/goods/filter", 429, 3600
        )
        now = timezone.now()
        self.assertEqual(run_due_checks(), 1)
        self.account.refresh_from_db()
        self.assertGreater(self.account.next_check_at, now + timedelta(minutes=59))
        self.assertTrue(self.account.check_requested)
        self.assertIsNone(self.account.initial_sync_completed_at)
        self.assertIn("429", self.account.last_error)
        due = self.account.next_check_at

        html = self.client.get(reverse("dashboard"))
        self.assertContains(html, "Ожидаем разрешения WB")
        self.assertContains(html, "Следующая автоматическая попытка")
        self.assertContains(html, 'disabled aria-disabled="true"')
        self.assertContains(html, 'id="wb-next-retry"')
        self.assertNotContains(html, 'type="submit" class="welcome-submit">Повторить загрузку')

        for _ in range(3):
            response = self.client.post(reverse("dashboard"), {
                "action": "retry_initial_sync",
            })
            self.assertRedirects(response, reverse("dashboard"))
            self.account.refresh_from_db()
            self.assertEqual(self.account.next_check_at, due)
            self.assertTrue(self.account.check_requested)

        self.assertEqual(run_due_checks(), 0)
        self.assertEqual(wb_class.return_value.get_products.call_count, 1)

    @patch("guard.monitor.WBClient")
    def test_reconnecting_token_does_not_reset_seller_429_cooldown(self, wb_class):
        wb_class.return_value.get_product_cards.return_value = []
        wb_class.return_value.get_products.side_effect = WBApiError(
            "WB HTTP 429", 429, 3600,
        )
        self.assertEqual(run_due_checks(), 1)
        self.account.refresh_from_db()
        due = self.account.next_check_at
        with patch("guard.integrations.WBClient"):
            response = self.client.post(
                reverse("connect_wb"), {"token": token_for(kind=1)}
            )
        self.assertRedirects(response, reverse("dashboard"))
        self.account.refresh_from_db()
        self.assertEqual(self.account.next_check_at, due)
        self.assertIn("429", self.account.last_error)
        self.assertIsNone(self.account.initial_sync_completed_at)
        self.assertTrue(self.account.check_requested)
        self.assertContains(self.client.get("/"), "Ожидаем разрешения WB")
        self.assertEqual(run_due_checks(), 0)

    @patch("guard.monitor.WBClient")
    def test_retry_is_only_queued_after_wait_expires(self, wb_class):
        wb_class.return_value.get_product_cards.return_value = []
        wb_class.return_value.get_products.side_effect = WBApiError(
            "WB HTTP 429", 429, 1200
        )
        self.assertEqual(run_due_checks(), 1)
        self.account.refresh_from_db()
        self.assertGreater(self.account.next_check_at, timezone.now() + timedelta(minutes=59))
        GuardAccount.objects.filter(pk=self.account.pk).update(
            next_check_at=timezone.now() - timedelta(seconds=1),
            check_requested=False,
        )
        response = self.client.post(reverse("dashboard"), {
            "action": "retry_initial_sync",
        })
        self.assertRedirects(response, reverse("dashboard"))
        self.account.refresh_from_db()
        self.assertTrue(self.account.check_requested)
        self.assertLessEqual(self.account.next_check_at, timezone.now())
        self.assertEqual(self.account.last_error, "")
