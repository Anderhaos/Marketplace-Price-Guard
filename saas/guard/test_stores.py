from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import GuardAccount, GuardEvent, Product, Upload


@override_settings(SAAS_WB_NETWORK_ENABLED=False, SAAS_ACCESS_ENFORCED=True)
class MultipleStoresTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice@stores.test", "alice@stores.test", "test-pass-123")
        self.bob = User.objects.create_user("bob@stores.test", "bob@stores.test", "test-pass-123")
        self.a1 = GuardAccount.objects.create(user=self.alice, name="WB Store A")
        self.a2 = GuardAccount.objects.create(user=self.alice, name="WB Store B")
        self.b1 = GuardAccount.objects.create(user=self.bob, name="Private WB")
        Product.objects.create(account=self.a1, nm_id=11111, vendor_code="shop-a")
        Product.objects.create(account=self.a2, nm_id=22222, vendor_code="shop-b")
        Product.objects.create(account=self.b1, nm_id=33333, vendor_code="private-bob")
        GuardEvent.objects.create(account=self.a1, kind="discount_changed", message="event-alpha")
        GuardEvent.objects.create(account=self.a2, kind="discount_changed", message="event-beta")
        Upload.objects.create(account=self.a1, upload_id=101)
        Upload.objects.create(account=self.a2, upload_id=102)
        self.client.force_login(self.alice)

    def test_shop_selector_changes_dashboard_and_history(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "shop-a")
        self.assertNotContains(response, "shop-b")
        self.assertEqual(self.client.post(reverse("select_store", args=[self.a2.pk])).status_code, 302)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "shop-b")
        self.assertNotContains(response, "shop-a")
        response = self.client.get(reverse("history"))
        self.assertContains(response, "event-beta")
        self.assertNotContains(response, "event-alpha")

    def test_cannot_select_or_view_another_users_store(self):
        self.assertEqual(self.client.post(reverse("select_store", args=[self.b1.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("stores")), "Private WB")
        self.client.session["mpno_selected_store_id"] = self.b1.pk
        self.assertNotContains(self.client.get(reverse("dashboard")), "private-bob")

    def test_cross_store_product_actions_are_inaccessible(self):
        self.assertEqual(
            self.client.post(reverse("product_rule", args=[22222]), {"allowed_discount": "5"}).status_code, 404
        )
        self.assertEqual(
            self.client.get(reverse("product_history_data", args=[22222])).status_code, 404
        )
        self.client.post(reverse("select_store", args=[self.a2.pk]))
        self.assertEqual(
            self.client.post(reverse("product_rule", args=[11111]), {"allowed_discount": "5"}).status_code, 404
        )

    def test_create_new_store_is_isolated_and_has_no_token(self):
        with patch("guard.views.WBClient") as wb:
            response = self.client.post(reverse("stores"), {"name": "  Ещё один WB  "})
        self.assertEqual(response.status_code, 302)
        account = GuardAccount.objects.get(user=self.alice, name="Ещё один WB")
        self.assertFalse(account.enabled)
        self.assertEqual(account.token_ciphertext, "")
        self.assertEqual(account.marketplace, "wb")
        wb.assert_not_called()

    def test_shop_switch_requires_post_and_csrf(self):
        self.assertEqual(self.client.get(reverse("select_store", args=[self.a2.pk])).status_code, 405)
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.alice)
        self.assertEqual(c.post(reverse("select_store", args=[self.a2.pk])).status_code, 403)

    def test_session_store_id_from_other_user_falls_back(self):
        session=self.client.session
        session["mpno_selected_store_id"]=self.b1.pk
        session.save()
        response=self.client.get(reverse("dashboard"))
        self.assertContains(response,"shop-a")
        self.assertNotContains(response,"private-bob")
