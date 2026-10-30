"""Buyer-facing navigation must not mix operator administration and seller tools."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant
from .models import GuardAccount, Product


@override_settings(SAAS_PIN_AUTH_ENABLED=False, SAAS_WB_NETWORK_ENABLED=False,
                   SAAS_ACCESS_ENFORCED=True)
class MarketplaceNavigationTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            "market-nav@example.test", "market-nav@example.test", "not-used-qa-pass",
        )
        self.operator = User.objects.create_superuser(
            "owner-nav@example.test", "owner-nav@example.test", "not-used-qa-pass",
        )
        self.shop = GuardAccount.objects.create(
            user=self.seller, marketplace="wb", name="Primary WB",
            initial_sync_completed_at=timezone.now(), token_ciphertext="opaque",
        )
        self.owner_shop = GuardAccount.objects.create(
            user=self.operator, marketplace="wb", name="Owner WB",
            initial_sync_completed_at=timezone.now(), token_ciphertext="opaque",
        )
        Product.objects.create(account=self.shop, nm_id=12345678, vendor_code="MY-WB-01", title="WB card")
        AccessGrant.objects.create(
            user=self.seller, plan="standard", source="admin", source_ref="marketplace-nav-qa",
            starts_at=timezone.now()-timedelta(days=1),
            ends_at=timezone.now()+timedelta(days=2),
        )
        self.client.force_login(self.seller)

    def test_marketplace_dropdown_is_honest_about_availability(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, 'aria-label="Выбор маркетплейса"')
        self.assertContains(response, "Wildberries")
        self.assertContains(response, "Ozon")
        self.assertContains(response, "Яндекс Маркет")
        self.assertContains(response, "Мегамаркет")
        self.assertContains(response, 'data-theme-preview="wb"')
        self.assertContains(response, 'data-theme-preview="ozon"')
        self.assertContains(response, 'data-theme-preview="yandex"')
        self.assertContains(response, 'data-theme-preview="mega"')
        self.assertContains(response, 'Предпросмотр оформления · данные WB', count=3)
        self.assertContains(response, 'id="theme-preview-banner"')
        self.assertContains(response, 'marketplace-previews.js')
        self.assertContains(response, 'marketplace-previews.css')
        self.assertNotContains(response, 'href="/ozon/"')
        self.assertNotContains(response, 'href="/admin/"')
        self.assertContains(response, 'href="/stores/"')
        self.assertContains(response, "Подключения")

    def test_wide_inventory_grid_only_on_authenticated_dashboard(self):
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, "seller-dashboard-page")
        self.assertContains(dashboard, "seller-dashboard-grid.css?v=20261030a")
        assistant = self.client.get(reverse("assistant_chat"), follow=True)
        self.assertNotContains(assistant, "seller-dashboard-page")
        self.assertNotContains(assistant, "seller-dashboard-grid.css")
        self.client.logout()
        public = self.client.get(reverse("login"))
        self.assertNotContains(public, "seller-dashboard-grid.css")

    def test_owner_has_no_operator_admin_link_in_seller_workspace(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-label="Основная навигация"')
        self.assertNotContains(response, '<a href="/admin/">Админка</a>')
        # Admin route itself remains available and protected by existing RBAC.

    def test_quick_links_use_official_wb_and_card_url(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, 'href="https://www.wildberries.ru/"')
        self.assertContains(response, 'href="https://seller.wildberries.ru/"')
        self.assertContains(response, 'href="https://www.wildberries.ru/catalog/12345678/detail.aspx"')
        self.assertContains(response, 'rel="noopener noreferrer"')

    def test_connections_page_explains_multiple_cabinets(self):
        response = self.client.get(reverse("stores"))
        self.assertContains(response, "Подключения магазинов")
        self.assertContains(response, "Подключить ещё один кабинет Wildberries")
        self.assertContains(response, "ничего добавлять не надо")
        self.assertContains(response, "Primary WB")

    def test_no_seller_chat_visible_in_navigation(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Сводки")
        self.assertNotContains(response, 'href="/assistant/"')
        retired = self.client.get(reverse("assistant_chat"))
        self.assertEqual(retired.status_code, 302)
        self.assertEqual(retired.url, reverse("notifications"))
        self.assertEqual(self.client.post(reverse("assistant_chat"), {
            "mode": "smart", "question": "AI",
        }).status_code, 405)
        feed = self.client.get(reverse("notifications"))
        self.assertEqual(feed.status_code, 200)
        self.assertNotContains(feed, 'id="assistant-question"')
        self.assertNotContains(feed, 'href="/admin/"')
