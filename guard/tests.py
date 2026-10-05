from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.test import Client
from django.urls import reverse

from .models import GuardAccount, Product, Upload
from .monitor import check_account, run_due_checks
from .wb import WBClient, get_token_permissions


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PortalTests(TestCase):
    def test_favicon_request_redirects_to_static_icon(self):
        response = self.client.get("/favicon.ico")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/favicon.svg")
        self.assertEqual(self.client.get("/favicon.svg")["Content-Type"], "image/svg+xml")

    @override_settings(REQUIRE_EMAIL_VERIFICATION=False)
    def test_registration_without_email_verification(self):
        response = self.client.post(reverse("register"), {
            "email": "seller@example.com", "password1": "Strong-Test-Password-903!", "password2": "Strong-Test-Password-903!",
        })
        self.assertRedirects(response, reverse("login"))
        self.assertTrue(User.objects.get(username="seller@example.com").is_active)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(self.client.login(username="seller@example.com", password="Strong-Test-Password-903!"))
        self.assertEqual(self.client.get(reverse("settings")).status_code, 200)

    def test_registration_requires_email_verification(self):
        response = self.client.post(reverse("register"), {
            "email": "seller@example.com", "password1": "Strong-Test-Password-903!", "password2": "Strong-Test-Password-903!",
        })
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(username="seller@example.com")
        self.assertFalse(user.is_active)
        self.assertEqual(len(mail.outbox), 1)
        link = mail.outbox[0].body.splitlines()[-1]
        self.assertEqual(self.client.get(link).status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_seller_cannot_change_another_sellers_product(self):
        alice = User.objects.create_user("alice@example.com", "alice@example.com", "Strong-Test-Password-903!")
        bob = User.objects.create_user("bob@example.com", "bob@example.com", "Strong-Test-Password-903!")
        a = GuardAccount.objects.create(user=alice)
        b = GuardAccount.objects.create(user=bob)
        Product.objects.create(account=a, nm_id=123, current_discount=20)
        other = Product.objects.create(account=b, nm_id=456, current_discount=20)
        self.client.force_login(alice)
        response = self.client.post(reverse("product_rule", args=[456]), {"allowed_discount": 50})
        self.assertEqual(response.status_code, 404)
        other.refresh_from_db()
        self.assertIsNone(other.allowed_discount_override)
        self.assertNotContains(self.client.get(reverse("dashboard")), "456")
        self.assertEqual(self.client.get(reverse("product_history_data", args=[456])).status_code, 404)

    def test_settings_post_requires_csrf_token(self):
        user = User.objects.create_user("seller@example.com", "seller@example.com", "Strong-Test-Password-903!")
        GuardAccount.objects.create(user=user)
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        response = client.post(reverse("settings"), {"action": "settings", "enabled": "on"})
        self.assertEqual(response.status_code, 403)

    def test_manual_check_queues_when_autoguard_is_disabled(self):
        user = User.objects.create_user("seller@example.com", "seller@example.com", "Strong-Test-Password-903!")
        account = GuardAccount.objects.create(user=user, token_ciphertext="ciphertext", enabled=False)
        self.client.force_login(user)
        response = self.client.post(reverse("settings"), {"action": "check"})
        self.assertRedirects(response, reverse("dashboard"))
        account.refresh_from_db()
        self.assertTrue(account.check_requested)

    def test_seller_sets_global_and_product_discount_limits(self):
        user = User.objects.create_user("seller@example.com", "seller@example.com", "Strong-Test-Password-903!")
        account = GuardAccount.objects.create(user=user, token_ciphertext="ciphertext")
        product = Product.objects.create(account=account, nm_id=123, current_discount=25)
        self.client.force_login(user)
        self.assertRedirects(
            self.client.post(reverse("dashboard"), {"action": "apply_all", "default_allowed_discount": 15}),
            reverse("dashboard"),
        )
        account.refresh_from_db()
        self.assertEqual(account.default_allowed_discount, 15)
        self.assertTrue(account.apply_all_requested)
        self.assertEqual(product.allowed_discount, 15)
        self.assertContains(self.client.get(reverse("dashboard")), "Предел скидок")
        self.client.post(reverse("product_rule", args=[123]), {"allowed_discount": 10})
        product.refresh_from_db()
        self.assertEqual(product.allowed_discount, 10)
        self.client.post(reverse("product_rule", args=[123]), {"allowed_discount": ""})
        product.refresh_from_db()
        self.assertIsNone(product.allowed_discount_override)
        self.assertEqual(product.allowed_discount, 15)

    def test_dashboard_can_toggle_autocorrection_and_set_no_discount_for_one_product(self):
        user = User.objects.create_user("seller@example.com", "seller@example.com", "Strong-Test-Password-903!")
        account = GuardAccount.objects.create(user=user, token_ciphertext="ciphertext")
        product = Product.objects.create(account=account, nm_id=123, current_discount=12)
        self.client.force_login(user)
        self.assertRedirects(self.client.post(reverse("dashboard"), {"action": "toggle_autocontrol", "enabled": "on"}), reverse("dashboard"))
        account.refresh_from_db()
        self.assertTrue(account.enabled)
        self.assertRedirects(self.client.post(reverse("product_rule", args=[123]), {"no_discount": "on"}), reverse("dashboard"))
        product.refresh_from_db()
        account.refresh_from_db()
        self.assertEqual(product.allowed_discount_override, 0)
        self.assertEqual(account.apply_requested_ids, [123])
        self.assertContains(self.client.get(reverse("dashboard")), "Отменено скидок")

    def test_dashboard_filters_products_by_query(self):
        user = User.objects.create_user("seller@example.com", "seller@example.com", "Strong-Test-Password-903!")
        account = GuardAccount.objects.create(user=user)
        Product.objects.create(account=account, nm_id=1, current_discount=20, allowed_discount_override=10, active=True)
        Product.objects.create(account=account, nm_id=2, current_discount=0, active=True)
        Upload.objects.create(account=account, upload_id=1, status="success", product_ids=[2], corrected_count=1)
        self.client.force_login(user)
        over = self.client.get(reverse("dashboard"), {"filter": "over_limit"})
        self.assertContains(over, "Показан фильтр")
        self.assertNotContains(over, "WB 2")
        corrected = self.client.get(reverse("dashboard"), {"filter": "corrected"})
        self.assertContains(corrected, "WB 2")
        self.assertNotContains(corrected, "WB 1")

    def test_apply_global_discount_queues_wb_update_for_all_products(self):
        user = User.objects.create_user("seller@example.com", "seller@example.com", "Strong-Test-Password-903!")
        account = GuardAccount.objects.create(user=user, token_ciphertext="ciphertext")
        Product.objects.create(account=account, nm_id=123, current_discount=12, allowed_discount_override=5)
        self.client.force_login(user)
        response = self.client.post(reverse("dashboard"), {"action": "apply_all", "default_allowed_discount": 0})
        self.assertRedirects(response, reverse("dashboard"))
        account.refresh_from_db()
        self.assertEqual(account.default_allowed_discount, 0)
        self.assertTrue(account.apply_all_requested)
        self.assertIsNone(Product.objects.get(account=account, nm_id=123).allowed_discount_override)

    @patch("guard.monitor.decrypt_token", return_value="fake-token")
    @patch("guard.monitor.WBClient")
    def test_apply_global_discount_submits_every_product_even_when_autoguard_is_off(self, client_class, _decrypt):
        user = User.objects.create_user("seller@example.com", "seller@example.com", "Strong-Test-Password-903!")
        account = GuardAccount.objects.create(user=user, token_ciphertext="ciphertext", apply_all_requested=True, default_allowed_discount=0)
        client = client_class.return_value
        client.get_products.return_value = [
            {"nmID": 123, "vendorCode": "A", "discount": 12, "sizes": [{"price": 1000}]},
            {"nmID": 456, "vendorCode": "B", "discount": 0, "sizes": [{"price": 1000}]},
        ]
        client.submit_discounts.return_value = 999
        self.assertEqual(run_due_checks(), 1)
        client.submit_discounts.assert_called_once_with([(123, 0), (456, 0)])
        account.refresh_from_db()
        self.assertFalse(account.apply_all_requested)

    @patch("guard.monitor.decrypt_token", return_value="fake-token")
    @patch("guard.monitor.WBClient")
    def test_individual_no_discount_submits_even_when_autoguard_is_off(self, client_class, _decrypt):
        user = User.objects.create_user("seller@example.com", "seller@example.com", "Strong-Test-Password-903!")
        account = GuardAccount.objects.create(user=user, token_ciphertext="ciphertext", apply_requested_ids=[123])
        Product.objects.create(account=account, nm_id=123, current_discount=12, allowed_discount_override=0)
        client = client_class.return_value
        client.get_products.return_value = [
            {"nmID": 123, "vendorCode": "A", "discount": 12, "sizes": [{"price": 1000}]},
            {"nmID": 456, "vendorCode": "B", "discount": 25, "sizes": [{"price": 1000}]},
        ]
        client.get_product_cards.return_value = []
        client.submit_discounts.return_value = 999
        self.assertEqual(run_due_checks(), 1)
        client.submit_discounts.assert_called_once_with([(123, 0)])

    @patch("guard.monitor.decrypt_token", return_value="fake-token")
    @patch("guard.monitor.WBClient")
    def test_manual_check_reads_products_without_changing_discounts(self, client_class, _decrypt):
        user = User.objects.create_user("seller@example.com", "seller@example.com", "Strong-Test-Password-903!")
        account = GuardAccount.objects.create(user=user, token_ciphertext="ciphertext", enabled=False, check_requested=True)
        client = client_class.return_value
        client.get_products.return_value = [{"nmID": 123, "vendorCode": "SKU", "discount": 25, "sizes": [{"price": 1000}]}]
        self.assertEqual(run_due_checks(), 1)
        account.refresh_from_db()
        self.assertFalse(account.check_requested)
        self.assertIsNotNone(account.last_check_at)
        self.assertEqual(Product.objects.get(account=account, nm_id=123).current_discount, 25)
        client.submit_discounts.assert_not_called()

    @patch("guard.monitor.decrypt_token", return_value="fake-token")
    @patch("guard.monitor.WBClient")
    def test_monitor_submits_discount_without_price_and_waits_for_confirmation(self, client_class, _decrypt):
        user = User.objects.create_user("seller@example.com", "seller@example.com", "Strong-Test-Password-903!")
        account = GuardAccount.objects.create(user=user, token_ciphertext="ciphertext", enabled=True)
        client = client_class.return_value
        client.get_products.return_value = [{"nmID": 123, "vendorCode": "SKU", "discount": 25, "sizes": [{"price": 1000}]}]
        client.submit_discounts.return_value = 999
        self.assertTrue(check_account(account))
        client.submit_discounts.assert_called_once_with([(123, 0)])
        self.assertEqual(Upload.objects.get(account=account).status, "pending")
        Product.objects.filter(account=account, nm_id=123).update(allowed_discount_override=7)
        client.get_upload_state.return_value = 3
        client.get_products.return_value = []
        self.assertFalse(check_account(account))
        self.assertEqual(Upload.objects.get(account=account).status, "success")
        product = Product.objects.get(account=account, nm_id=123)
        self.assertFalse(product.active)
        self.assertEqual(product.allowed_discount_override, 7)

    @patch("guard.monitor.decrypt_token", return_value="fake-token")
    @patch("guard.monitor.WBClient")
    def test_disabled_account_still_reconciles_pending_upload(self, client_class, _decrypt):
        user = User.objects.create_user("seller@example.com", "seller@example.com", "Strong-Test-Password-903!")
        account = GuardAccount.objects.create(user=user, token_ciphertext="ciphertext", enabled=False)
        Upload.objects.create(account=account, upload_id=999, product_ids=[123])
        client = client_class.return_value
        client.get_upload_state.return_value = 3
        self.assertEqual(run_due_checks(), 1)
        self.assertEqual(Upload.objects.get(account=account).status, "success")
        client.get_products.assert_called_once()


class WBClientTests(TestCase):
    def test_extracts_displayable_token_permissions_without_exposing_token(self):
        import base64
        import json

        payload = base64.urlsafe_b64encode(json.dumps({"s": (1 << 1) | (1 << 3)}).encode()).decode().rstrip("=")
        permissions = get_token_permissions(f"header.{payload}.signature")
        self.assertEqual(permissions["access"], "Чтение и запись")
        self.assertEqual(permissions["categories"], ["Контент", "Цены и скидки"])

    def test_fetches_all_pages(self):
        client = WBClient("fake-token")
        calls = []

        def fake_request(method, path, params=None, body=None):
            calls.append(params["offset"])
            return {"data": {"listGoods": [{"nmID": i} for i in range(1000)] if params["offset"] == 0 else [{"nmID": 1001}] if params["offset"] == 1000 else []}}

        client._request = fake_request
        self.assertEqual(len(client.get_products()), 1001)
        self.assertEqual(calls, [0, 1000, 2000])
