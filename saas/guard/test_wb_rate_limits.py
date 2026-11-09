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


@override_settings(SAAS_WB_READ_ONLY=True, SAAS_WB_NETWORK_ENABLED=True)
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
        self.assertContains(html, "auth-flow-retry-box")
        self.assertContains(html, 'id="wb-retry-countdown"')
        self.assertContains(html, "(МСК)")
        self.assertContains(html, "ваше местное время")
        self.assertNotContains(html, "WB HTTP 429 at GET")
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


@override_settings(
    SAAS_PIN_AUTH_ENABLED=False, SAAS_ACCESS_ENFORCED=False,
    SAAS_WB_NETWORK_ENABLED=True, SAAS_WB_READ_ONLY=False,
)
class BasicTokenStagedCorrectionsTests(TestCase):
    """One WB prices API call per >=16-minute cycle, no immediate GET+POST."""
    def setUp(self):
        self.user = User.objects.create_user("basic-stage@example.test")
        self.account = GuardAccount.objects.create(
            user=self.user, token_ciphertext="encrypted-fixture",
            enabled=True, default_allowed_discount=13,
            max_fixes=20, check_requested=True,
        )
        self.token = token_for(kind=1)
        self.price_listing = [
            {"nmID": 123, "vendorCode": "SKU-A", "discount": 20,
             "sizes": [{"price": 1000}]}
        ]

    def due(self):
        GuardAccount.objects.filter(pk=self.account.pk).update(
            next_check_at=timezone.now() - timedelta(seconds=1),
        )

    @patch("guard.monitor.decrypt_token")
    @patch("guard.monitor.WBClient")
    def test_basic_phases_read_then_submit_then_confirm_then_verify(self, wb_factory, decrypt):
        decrypt.return_value = self.token
        client = wb_factory.return_value
        client.get_product_cards.return_value = []
        client.get_products.return_value = self.price_listing
        client.submit_discounts.return_value = 987
        client.get_upload_state.return_value = 3
        now = timezone.now()

        self.assertEqual(run_due_checks(), 1)
        self.account.refresh_from_db()
        self.assertEqual(self.account.basic_staged_changes, [[123, 13]])
        self.assertGreater(self.account.next_check_at, now + timedelta(minutes=15))
        self.assertTrue(self.account.check_requested)
        client.get_products.assert_called_once()
        client.submit_discounts.assert_not_called()
        client.get_upload_state.assert_not_called()

        self.due()
        self.assertEqual(run_due_checks(), 1)
        self.account.refresh_from_db()
        self.assertEqual(self.account.basic_staged_changes, [])
        from .models import Upload, Product
        upload = Upload.objects.get(account=self.account)
        self.assertEqual(upload.status, "pending")
        self.assertEqual(upload.corrected_count, 1)
        client.submit_discounts.assert_called_once_with([(123, 13)])
        client.get_products.assert_called_once()
        client.get_upload_state.assert_not_called()

        self.due()
        self.assertEqual(run_due_checks(), 1)
        upload.refresh_from_db()
        self.assertEqual(upload.status, "success")
        client.get_upload_state.assert_called_once_with(987)
        client.get_products.assert_called_once()

        client.get_products.return_value = [{
            **self.price_listing[0], "discount": 13,
        }]
        self.due()
        self.assertEqual(run_due_checks(), 1)
        self.assertEqual(Product.objects.get(account=self.account, nm_id=123).current_discount, 13)
        self.assertEqual(client.get_products.call_count, 2)
        client.submit_discounts.assert_called_once()

    @patch("guard.monitor.decrypt_token")
    @patch("guard.monitor.WBClient")
    def test_rules_change_invalidates_staged_write(self, wb_factory, decrypt):
        from django.db.models import F
        decrypt.return_value = self.token
        client = wb_factory.return_value
        client.get_product_cards.return_value = []
        client.get_products.return_value = self.price_listing
        self.assertEqual(run_due_checks(), 1)
        GuardAccount.objects.filter(pk=self.account.pk).update(
            rules_version=F("rules_version") + 1, default_allowed_discount=6,
        )
        self.due()
        self.assertEqual(run_due_checks(), 1)
        client.submit_discounts.assert_not_called()
        self.account.refresh_from_db()
        self.assertEqual(self.account.basic_staged_changes, [])
        self.assertTrue(self.account.check_requested)

    @patch("guard.monitor.decrypt_token")
    @patch("guard.monitor.WBClient")
    def test_429_during_submit_preserves_stage_and_hour_cooldown(self, wb_factory, decrypt):
        decrypt.return_value = self.token
        client = wb_factory.return_value
        client.get_product_cards.return_value = []
        client.get_products.return_value = self.price_listing
        client.submit_discounts.side_effect = WBApiError("Too many requests",429,3600)
        self.assertEqual(run_due_checks(), 1)
        self.due()
        now=timezone.now()
        self.assertEqual(run_due_checks(), 1)
        self.account.refresh_from_db()
        self.assertEqual(self.account.basic_staged_changes, [[123,13]])
        self.assertGreater(self.account.next_check_at, now + timedelta(minutes=59))
        self.assertEqual(run_due_checks(), 0)
        client.submit_discounts.assert_called_once()

    @patch("guard.monitor.decrypt_token")
    @patch("guard.monitor.WBClient")
    def test_manual_apply_all_survives_20_item_upload_batches(self, wb_factory, decrypt):
        from .models import Upload
        decrypt.return_value = self.token
        GuardAccount.objects.filter(pk=self.account.pk).update(
            enabled=False, apply_all_requested=True, max_fixes=20,
        )
        client=wb_factory.return_value
        client.get_product_cards.return_value=[]
        client.get_products.return_value=[
            {"nmID":i, "discount":25, "sizes":[{"price":1000}]} for i in range(1,26)
        ]
        client.submit_discounts.return_value=10010
        client.get_upload_state.return_value=3
        self.assertEqual(run_due_checks(), 1)
        self.account.refresh_from_db()
        self.assertEqual(len(self.account.basic_staged_changes),25)
        self.due()
        self.assertEqual(run_due_checks(),1)
        self.assertEqual(len(client.submit_discounts.call_args.args[0]),20)
        self.due()
        self.assertEqual(run_due_checks(),1)
        self.account.refresh_from_db()
        self.assertTrue(self.account.apply_all_requested)
        self.assertTrue(self.account.check_requested)
        self.assertEqual(Upload.objects.get(account=self.account).status,"success")
        # WB now reports the first 20 discounts corrected, five remain.
        client.get_products.return_value=[
            {"nmID":i, "discount":13 if i<=20 else 25, "sizes":[{"price":1000}]}
            for i in range(1,26)
        ]
        self.due()
        self.assertEqual(run_due_checks(),1)
        self.account.refresh_from_db()
        self.assertEqual(len(self.account.basic_staged_changes),5)
        self.assertEqual(client.submit_discounts.call_count,1)

    @patch("guard.monitor.decrypt_token")
    @patch("guard.monitor.WBClient")
    def test_auto_guard_disabled_before_upload_never_writes(self, wb_factory, decrypt):
        decrypt.return_value = self.token
        client=wb_factory.return_value
        client.get_product_cards.return_value=[]
        client.get_products.return_value=self.price_listing
        self.assertEqual(run_due_checks(),1)
        GuardAccount.objects.filter(pk=self.account.pk).update(enabled=False)
        self.due()
        self.assertEqual(run_due_checks(),1)
        client.submit_discounts.assert_not_called()
        self.account.refresh_from_db()
        self.assertEqual(self.account.basic_staged_changes,[])


class BasicManualActionCooldownTests(SimpleTestCase):
    @patch("guard.views._account_token_type", return_value="base")
    def test_manual_changes_do_not_bypass_fifteen_minute_window(self, _token):
        from types import SimpleNamespace
        from .views import _scheduled_wb_due
        now = timezone.now()
        account = SimpleNamespace(
            last_error="", last_check_at=now,
            next_check_at=now + timedelta(minutes=16),
        )
        self.assertEqual(_scheduled_wb_due(account,now),now+timedelta(minutes=16))
        account.last_error="Wildberries HTTP 429"
        account.next_check_at=now+timedelta(hours=1)
        self.assertEqual(_scheduled_wb_due(account,now),now+timedelta(hours=1))
