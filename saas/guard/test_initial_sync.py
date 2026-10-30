"""A verified API token is necessary, but a completed read-only import is mandatory."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import GuardAccount, Product
from billing.models import AccessGrant
from billing.trial import TRIAL_DAYS, grant_trial_once
from .monitor import run_due_checks
from .test_onboarding_trial import token_for
from .wb import WBApiError


@override_settings(
    SAAS_PIN_AUTH_ENABLED=True, SAAS_ACCESS_ENFORCED=True,
    SAAS_WB_TOKEN_VALIDATION_ENABLED=True, SAAS_WB_NETWORK_ENABLED=True,
)
class InitialSyncGateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "initial@example.test", email="initial@example.test", password="validtest9632",
        )
        self.client.force_login(self.user)

    def connect(self, kind=1):
        with patch("guard.integrations.WBClient") as verifier:
            response = self.client.post(reverse("connect_wb"), {"token": token_for(kind=kind)})
        self.assertRedirects(response, reverse("dashboard"))
        verifier.return_value.validate_token.assert_called_once()
        return GuardAccount.objects.get(user=self.user)

    def test_before_api_only_the_connection_wizard_is_available(self):
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, 'id="wb-setup"')
        self.assertNotContains(r, 'class="compact-summary"')
        self.assertNotContains(r, 'href="/stores/"')
        for path in ("/stores/", "/settings/", "/history/", "/notifications/", "/billing/",
                     "/push/preferences/", "/products/44/history-data/"):
            with self.subTest(path=path):
                self.assertRedirects(self.client.get(path), reverse("dashboard"))
        self.assertEqual(self.client.post("/", {"action": "toggle_autocontrol"}).status_code, 405)

    def test_connection_queues_import_but_cannot_open_dashboard_yet(self):
        account = self.connect()
        self.assertIsNone(account.initial_sync_completed_at)
        self.assertTrue(account.check_requested)
        self.assertFalse(account.enabled)
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())
        r = self.client.get("/")
        self.assertContains(r, "Загружаем товары")
        self.assertContains(r, "Первичная синхронизация")
        self.assertNotContains(r, 'class="compact-summary"')
        self.assertNotContains(r, 'href="/stores/"')
        self.assertRedirects(self.client.get("/settings/"), "/")
        self.assertRedirects(self.client.get("/billing/"), "/")
        self.assertEqual(self.client.post("/", {"action": "apply_all"}).status_code, 405)

    @patch("guard.monitor.WBClient")
    def test_worker_loads_readonly_prices_and_content_before_dashboard_opens(self, wb_class):
        account = self.connect()
        wb = wb_class.return_value
        wb.get_product_cards.return_value = [
            {"nmID": 4201, "title": "Первый товар", "photos": [{"big": "https://example.test/a.jpg"}]}
        ]
        wb.get_products.return_value = [
            {"nmID": 4201, "discount": 17, "vendorCode": "sku42",
             "sizes": [{"price": 1250, "discountedPrice": 1037.5}]}
        ]
        self.assertEqual(run_due_checks(), 1)
        account.refresh_from_db()
        self.assertIsNotNone(account.initial_sync_completed_at)
        self.assertFalse(account.enabled)
        grant = AccessGrant.objects.get(user=self.user, source="trial")
        self.assertAlmostEqual((grant.ends_at - grant.starts_at).total_seconds(),
                               TRIAL_DAYS * 86400, delta=1)
        self.assertGreaterEqual(grant.starts_at, account.initial_sync_completed_at)
        product = Product.objects.get(account=account, nm_id=4201)
        self.assertEqual(product.title, "Первый товар")
        self.assertEqual(product.current_discount, 17)
        self.assertEqual(product.price, 1250)
        wb.submit_discounts.assert_not_called()
        r = self.client.get("/")
        self.assertContains(r, "Первый товар")
        self.assertContains(r, 'class="compact-summary"')
        self.assertNotContains(r, "Первичная синхронизация")
        self.assertEqual(self.client.get("/settings/").status_code, 200)

    @patch("guard.monitor.WBClient")
    def test_catalog_failure_cannot_mark_inventory_ready(self, wb_class):
        account = self.connect()
        wb_class.return_value.get_product_cards.side_effect = WBApiError("cards temporarily unavailable", 503)
        self.assertEqual(run_due_checks(), 1)
        account.refresh_from_db()
        self.assertIsNone(account.initial_sync_completed_at)
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())
        self.assertTrue(account.last_error)
        self.assertFalse(Product.objects.filter(account=account).exists())
        r = self.client.get("/")
        self.assertContains(r, "Повторная загрузка запланирована")
        self.assertContains(r, "Ожидаем разрешённого времени")
        self.assertContains(r, 'disabled aria-disabled="true"')
        self.assertNotContains(r, 'class="compact-summary"')

    @patch("guard.monitor.WBClient")
    def test_empty_catalog_is_valid_only_after_successful_complete_read(self, wb_class):
        account = self.connect()
        wb_class.return_value.get_product_cards.return_value = []
        wb_class.return_value.get_products.return_value = []
        self.assertEqual(run_due_checks(), 1)
        account.refresh_from_db()
        self.assertIsNotNone(account.initial_sync_completed_at)
        self.assertEqual(AccessGrant.objects.filter(user=self.user, source="trial").count(), 1)
        self.assertEqual(Product.objects.filter(account=account).count(), 0)
        self.assertContains(self.client.get("/"), "Товары появятся после первой проверки")

    @patch("guard.monitor.WBClient")
    def test_failed_first_import_can_retry_without_burning_trial(self, wb_class):
        account = self.connect()
        wb = wb_class.return_value
        wb.get_product_cards.side_effect = [WBApiError("temporary outage", 503), []]
        wb.get_products.return_value = [{
            "nmID": 712, "discount": 12,
            "sizes": [{"price": 1000, "discountedPrice": 880}],
        }]
        self.assertEqual(run_due_checks(), 1)
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())
        account.refresh_from_db()
        self.assertIsNone(account.initial_sync_completed_at)
        GuardAccount.objects.filter(pk=account.pk).update(next_check_at=timezone.now())
        self.assertEqual(run_due_checks(), 1)
        account.refresh_from_db()
        self.assertIsNotNone(account.initial_sync_completed_at)
        self.assertEqual(AccessGrant.objects.filter(user=self.user, source="trial").count(), 1)
        self.assertEqual(run_due_checks(), 0)

    @patch("guard.monitor.WBClient")
    def test_expired_trial_cannot_get_new_free_import(self, wb_class):
        from billing.trial import grant_trial_once
        self.connect()
        grant, _ = grant_trial_once(self.user.pk, at=timezone.now() - timedelta(days=12))
        self.assertLess(grant.ends_at, timezone.now())
        account = self.connect()
        wb_class.return_value.get_product_cards.return_value = []
        wb_class.return_value.get_products.return_value = []
        self.assertEqual(run_due_checks(), 0)
        account.refresh_from_db()
        self.assertIsNone(account.initial_sync_completed_at)
        self.assertEqual(AccessGrant.objects.filter(user=self.user, source="trial").count(), 1)
        wb_class.return_value.get_products.assert_not_called()

    def test_token_replacement_resets_readiness_and_pending_import(self):
        account = self.connect()
        account.initial_sync_completed_at = timezone.now()
        account.last_check_at = timezone.now()
        account.save(update_fields=["initial_sync_completed_at", "last_check_at"])
        self.assertEqual(self.client.get("/settings/").status_code, 200)
        self.connect()
        account.refresh_from_db()
        self.assertIsNone(account.initial_sync_completed_at)
        self.assertIsNone(account.last_check_at)
        self.assertRedirects(self.client.get("/settings/"), "/")
        self.assertContains(self.client.get("/"), "Загружаем товары")

    def test_selected_shop_must_be_ready_not_just_any_shop(self):
        ready = self.connect()
        ready.initial_sync_completed_at = timezone.now()
        ready.save(update_fields=["initial_sync_completed_at"])
        second = GuardAccount.objects.create(user=self.user, name="Новый магазин")
        sess = self.client.session
        sess["mpno_selected_store_id"] = second.pk
        sess.save()
        self.assertRedirects(self.client.get("/stores/"), "/")
        self.assertContains(self.client.get("/"), 'id="wb-setup"')
        sess = self.client.session
        sess["mpno_selected_store_id"] = ready.pk
        sess.save()
        self.assertEqual(self.client.get("/stores/").status_code, 200)

    @override_settings(SAAS_WB_READ_ONLY=True)
    @patch("guard.monitor.WBClient")
    def test_preview_reads_service_token_inventory_without_any_price_writes(self, wb_class):
        account = self.connect(kind=4)
        # Simulate an existing paid/trial account with a stale write flag.
        # The first-free-import path itself refuses such write intent.
        grant_trial_once(self.user.pk)
        account.enabled = True
        account.apply_all_requested = True
        account.save(update_fields=["enabled", "apply_all_requested"])
        wb = wb_class.return_value
        wb.get_product_cards.return_value = []
        wb.get_products.return_value = [
            {"nmID": 9903, "discount": 33, "vendorCode": "readonly-33",
             "sizes": [{"price": 500, "discountedPrice": 335}]},
        ]
        self.assertEqual(run_due_checks(), 1)
        account.refresh_from_db()
        self.assertIsNotNone(account.initial_sync_completed_at)
        self.assertFalse(account.enabled)
        self.assertFalse(account.apply_all_requested)
        self.assertTrue(Product.objects.filter(account=account, nm_id=9903).exists())
        wb.submit_discounts.assert_not_called()

    @override_settings(SAAS_WB_READ_ONLY=True)
    def test_preview_refuses_dashboard_write_controls_after_first_import(self):
        account = self.connect(kind=4)
        account.initial_sync_completed_at = timezone.now()
        account.save(update_fields=["initial_sync_completed_at"])
        response = self.client.get("/")
        self.assertContains(response, "Предельные скидки можно настроить")
        self.assertContains(response, 'name="action" value="save_limit"')
        self.assertContains(response, "disabled")
        for action in ("apply_all", "toggle_autocontrol"):
            response = self.client.post("/", {"action": action,
                                              "enabled": "on", "default_allowed_discount": "0"})
            self.assertRedirects(response, "/")
            account.refresh_from_db()
            self.assertFalse(account.apply_all_requested)
            self.assertFalse(account.enabled)

    @override_settings(SAAS_WB_NETWORK_ENABLED=False)
    def test_preview_explains_disabled_real_import_without_inventing_products(self):
        # Token was validated before the operator turned WB network off.
        with override_settings(SAAS_WB_NETWORK_ENABLED=True):
            self.connect()
        r = self.client.get("/")
        self.assertContains(r, "В изолированном тестовом окружении запросы Wildberries отключены")
        self.assertNotContains(r, 'class="compact-summary"')
        self.assertRedirects(self.client.post("/", {"action": "retry_initial_sync"}), "/")
