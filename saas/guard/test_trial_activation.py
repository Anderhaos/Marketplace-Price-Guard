"""One-shot, explicit NoSale trial after safe, configurable WB import."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.access import has_active_access
from billing.models import AccessGrant
from billing.trial import TRIAL_DAYS
from .models import GuardAccount, Product
from .monitor import run_due_checks
from .test_onboarding_trial import token_for


@override_settings(
    SAAS_PIN_AUTH_ENABLED=True, SAAS_ACCESS_ENFORCED=True,
    SAAS_WB_TOKEN_VALIDATION_ENABLED=True,
    SAAS_WB_NETWORK_ENABLED=True, SAAS_WB_READ_ONLY=False,
)
class TrialActivationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("trial-activate@example.test",
                                              "trial-activate@example.test", "1234")
        self.client.force_login(self.user)

    def import_one(self):
        with patch("guard.integrations.WBClient") as validator:
            self.client.post(reverse("connect_wb"), {"token": token_for(kind=1)})
            validator.return_value.validate_token.assert_called_once()
        with patch("guard.monitor.WBClient") as wb:
            wb.return_value.get_product_cards.return_value = [
                {"nmID": 9001, "title": "Тестовый товар NoSale", "photos": []}
            ]
            wb.return_value.get_products.return_value = [
                {"nmID": 9001, "discount": 7, "sizes": [
                    {"price": 1000, "discountedPrice": 930}
                ]}
            ]
            self.assertEqual(run_due_checks(), 1)
            wb.return_value.submit_discounts.assert_not_called()
        account = GuardAccount.objects.get(user=self.user)
        self.assertIsNotNone(account.initial_sync_completed_at)
        self.assertFalse(account.enabled)
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())
        return account

    def form(self, action="start_trial", **values):
        payload = {
            "action": action,
            "default_allowed_discount": "12",
            "limit_9001": "4",
        }
        payload.update(values)
        return payload

    def test_import_does_not_start_trial_or_allow_private_pages(self):
        self.import_one()
        self.assertFalse(has_active_access(self.user.pk))
        response = self.client.get("/")
        self.assertContains(response, "Настройте защиту товаров")
        self.assertContains(response, "Включить защиту · 3 дня бесплатно")
        self.assertNotContains(response, 'class="compact-summary"')
        for path in ("/settings/", "/stores/", "/history/", "/notifications/",
                     "/billing/", "/promotions/", "/finance/", "/advisor/"):
            with self.subTest(path=path):
                self.assertRedirects(self.client.get(path), "/")

    def test_save_rules_is_local_only_and_does_not_consume_trial(self):
        account = self.import_one()
        with patch("guard.monitor.WBClient") as remote:
            response = self.client.post("/", self.form("save_setup"))
            self.assertRedirects(response, "/")
            remote.assert_not_called()
        account.refresh_from_db()
        self.assertEqual(account.default_allowed_discount, 12)
        self.assertFalse(account.enabled)
        self.assertFalse(account.apply_all_requested)
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())
        self.assertEqual(Product.objects.get(account=account, nm_id=9001).allowed_discount_override, 4)
        self.assertContains(self.client.get("/"), "Настройте защиту товаров")

    def test_explicit_activation_grants_exactly_three_days_once(self):
        account = self.import_one()
        start = timezone.now()
        self.assertRedirects(self.client.post("/", self.form()), "/")
        end = timezone.now()
        account.refresh_from_db()
        self.assertTrue(account.enabled)
        self.assertTrue(has_active_access(self.user.pk))
        grant = AccessGrant.objects.get(user=self.user, source="trial")
        self.assertEqual(grant.plan, "standard")
        self.assertAlmostEqual((grant.ends_at - grant.starts_at).total_seconds(),
                               TRIAL_DAYS * 86400, delta=1)
        self.assertGreaterEqual(grant.starts_at, start)
        self.assertLessEqual(grant.starts_at, end)
        original_end = grant.ends_at
        response = self.client.post("/", self.form())
        self.assertEqual(response.status_code, 405)
        grant.refresh_from_db()
        self.assertEqual(grant.ends_at, original_end)
        self.assertEqual(AccessGrant.objects.filter(user=self.user, source="trial").count(), 1)
        self.assertContains(self.client.get("/"), 'class="compact-summary"')
        grant.ends_at = timezone.now() - timedelta(seconds=1)
        grant.save(update_fields=["ends_at"])
        self.assertContains(self.client.get("/"), 'id="nosale-trial-end-dialog"')
        self.assertFalse(has_active_access(self.user.pk))

    def test_zero_limit_requires_explicit_confirmation(self):
        account = self.import_one()
        self.assertRedirects(self.client.post("/", self.form(
            default_allowed_discount="0", limit_9001="0")), "/")
        account.refresh_from_db()
        self.assertEqual(account.default_allowed_discount, 0)
        self.assertFalse(account.enabled)
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())
        self.assertRedirects(self.client.post("/", self.form(
            default_allowed_discount="0", limit_9001="0",
            confirm_zero_discount="1")), "/")
        account.refresh_from_db()
        self.assertTrue(account.enabled)
        self.assertTrue(has_active_access(self.user.pk))

    def test_invalid_and_cross_tenant_product_values_never_start_trial(self):
        account = self.import_one()
        foreign = User.objects.create_user("foreign@example.test", password="1234")
        other = GuardAccount.objects.create(user=foreign, name="foreign")
        foreign_product = Product.objects.create(account=other, nm_id=9002,
                                                  current_discount=10, allowed_discount_override=17)
        self.assertRedirects(self.client.post("/", self.form(limit_9001="999")), "/")
        self.assertRedirects(self.client.post("/", self.form(limit_9001="abc")), "/")
        incomplete = self.form()
        incomplete.pop("limit_9001")
        self.assertRedirects(self.client.post("/", incomplete), "/")
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())
        self.assertRedirects(self.client.post("/", self.form(
            action="save_setup", limit_9002="0")), "/")
        foreign_product.refresh_from_db()
        self.assertEqual(foreign_product.allowed_discount_override, 17)
        self.assertFalse(GuardAccount.objects.get(pk=account.pk).enabled)

    @override_settings(SAAS_WB_READ_ONLY=True)
    def test_preview_can_save_locally_but_cannot_activate(self):
        account = self.import_one()
        self.assertRedirects(self.client.post("/", self.form("save_setup")), "/")
        self.assertContains(self.client.get("/"), 'disabled aria-disabled="true"')
        self.assertRedirects(self.client.post("/", self.form()), "/")
        account.refresh_from_db()
        self.assertFalse(account.enabled)
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())

    def test_cannot_activate_before_real_import_or_with_empty_catalog(self):
        self.assertEqual(self.client.post("/", self.form()).status_code, 405)
        with patch("guard.integrations.WBClient") as validator:
            self.client.post(reverse("connect_wb"), {"token": token_for(kind=1)})
            validator.return_value.validate_token.assert_called_once()
        self.assertEqual(self.client.post("/", self.form()).status_code, 405)
        # Real import succeeded, but no active products: do not burn free days.
        with patch("guard.monitor.WBClient") as wb:
            wb.return_value.get_product_cards.return_value = []
            wb.return_value.get_products.return_value = []
            self.assertEqual(run_due_checks(), 1)
        self.assertRedirects(self.client.post("/", {
            "action": "start_trial", "default_allowed_discount": "12",
        }), "/")
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())
        self.assertContains(self.client.get("/"), "В магазине пока нет активных товаров")

    def test_empty_initial_catalog_can_be_refreshed_without_starting_trial(self):
        with patch("guard.integrations.WBClient"):
            self.client.post(reverse("connect_wb"), {"token": token_for(kind=1)})
        with patch("guard.monitor.WBClient") as wb:
            wb.return_value.get_product_cards.return_value = []
            wb.return_value.get_products.return_value = []
            self.assertEqual(run_due_checks(), 1)
        account = GuardAccount.objects.get(user=self.user)
        self.assertIsNotNone(account.initial_sync_completed_at)
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())
        self.assertContains(self.client.get("/"), "Обновить список товаров")
        self.assertRedirects(self.client.post("/", {"action": "retry_empty_import"}), "/")
        account.refresh_from_db()
        self.assertFalse(account.check_requested)  # Cannot bypass the WB cooldown.
        GuardAccount.objects.filter(pk=account.pk).update(
            next_check_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertRedirects(self.client.post("/", {"action": "retry_empty_import"}), "/")
        account.refresh_from_db()
        self.assertTrue(account.check_requested)
        with patch("guard.monitor.WBClient") as wb:
            wb.return_value.get_product_cards.return_value = [
                {"nmID": 9001, "title": "Новый товар", "photos": []}
            ]
            wb.return_value.get_products.return_value = [
                {"nmID": 9001, "discount": 4, "sizes": [
                    {"price": 100, "discountedPrice": 96}
                ]}
            ]
            self.assertEqual(run_due_checks(), 1)
            wb.return_value.submit_discounts.assert_not_called()
        self.assertFalse(AccessGrant.objects.filter(user=self.user).exists())
        self.assertContains(self.client.get("/"), "Новый товар")
        self.assertRedirects(self.client.post("/", self.form()), "/")
        self.assertTrue(has_active_access(self.user.pk))

    def test_expired_old_grant_does_not_reset_by_setup_or_reconnect(self):
        account = self.import_one()
        self.client.post("/", self.form())
        trial = AccessGrant.objects.get(user=self.user, source="trial")
        old_end = timezone.now() - timedelta(days=1)
        trial.ends_at = old_end
        trial.save(update_fields=["ends_at"])
        self.assertEqual(self.client.post("/", self.form()).status_code, 405)
        with patch("guard.integrations.WBClient") as validator:
            self.client.post(reverse("connect_wb"), {"token": token_for(kind=1)})
            validator.return_value.validate_token.assert_called_once()
        trial.refresh_from_db()
        self.assertEqual(trial.ends_at, old_end)
        self.assertEqual(AccessGrant.objects.filter(user=self.user, source="trial").count(), 1)
