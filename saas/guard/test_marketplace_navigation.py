"""Buyer-facing navigation must not mix operator administration and seller tools."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant
from billing.trial import trial_source_ref
from .models import GuardAccount, Product, PromotionSnapshot, FinanceSnapshot
from push.models import NotificationPreference, PushMessage


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

    def test_seller_menu_only_shows_working_wb_sections(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Wildberries")
        self.assertNotContains(response, '<a href="/stores/"><strong>')
        self.assertContains(response, 'href="/settings/"')
        self.assertNotContains(response, 'href="/finance/"')
        self.assertNotContains(response, 'href="/promotions/"')
        self.assertContains(response, "Уведомления")
        self.assertContains(response, 'class="seller-more-menu"')
        self.assertContains(response, 'href="/notifications/"', count=1)
        self.assertContains(response, 'class="nav-primary nav-notifications ')
        self.assertContains(response, 'class="nav-notification-count">0</span>')
        self.assertNotContains(response, '<strong>Уведомления')
        self.assertNotContains(response, 'aria-label="Выбор маркетплейса"')
        self.assertNotContains(response, 'data-theme-preview=')
        self.assertNotContains(response, 'id="theme-preview-banner"')
        self.assertNotContains(response, 'marketplace-previews.js')
        self.assertNotContains(response, 'marketplace-previews.css')
        self.assertNotContains(response, 'href="/ozon/"')
        self.assertNotContains(response, 'href="/admin/"')
        self.assertNotContains(response, 'Сводки')
        self.assertNotContains(response, "Сменить магазин")

    def test_trial_status_is_in_header_on_all_seller_pages_not_under_store(self):
        grant = AccessGrant.objects.get(user=self.seller)
        grant.source = "trial"
        grant.source_ref = trial_source_ref(self.seller.pk)
        grant.save(update_fields=["source", "source_ref"])
        for url in (reverse("dashboard"), reverse("settings"), reverse("notifications")):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                self.assertEqual(html.count('class="trial-strip seller-header-access"'), 1)
                self.assertEqual(html.count("Пробный доступ"), 1)
                self.assertLess(
                    html.index('class="trial-strip seller-header-access"'),
                    html.index('aria-label="Основная навигация"'),
                )
                self.assertGreater(
                    html.index('class="trial-strip seller-header-access"'),
                    html.index('class="brand"'),
                )
                self.assertNotIn('class="compact-context">\n  <span class="trial-strip"', html)
        self.assertContains(self.client.get(reverse("dashboard")),
                            "seller-workspace-panels.css?v=20261101d")

    def test_expired_trial_badge_stays_in_header_and_is_short_on_mobile(self):
        grant = AccessGrant.objects.get(user=self.seller)
        grant.source = "trial"
        grant.source_ref = trial_source_ref(self.seller.pk)
        grant.ends_at = timezone.now() - timedelta(hours=1)
        grant.save(update_fields=["source", "source_ref", "ends_at"])
        page = self.client.get(reverse("notifications"))
        self.assertContains(page, "Пробный период завершён", count=1)
        self.assertContains(page, 'class="trial-strip trial-strip--expired seller-header-access"')
        self.assertContains(page, 'class="seller-header-access__until"')

    def test_public_and_pretrial_header_have_no_trial_badge(self):
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("dashboard")), "seller-header-access")
        self.client.force_login(self.seller)
        AccessGrant.objects.filter(user=self.seller).delete()
        self.assertNotContains(self.client.get(reverse("dashboard")), 'class="trial-strip seller-header-access"')

    def test_discount_controls_are_compact_and_accessibly_labelled(self):
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, 'name="enabled" aria-label="Защита скидок"')
        self.assertNotContains(page, '<span class="toggle-track"></span><b>')
        self.assertContains(
            page,
            '<label for="dashboard-discount-limit" class="summary-label">Предел скидки, %</label>',
        )
        self.assertContains(page, 'id="dashboard-discount-limit" type="number"')
        self.assertContains(page, "seller-workspace-panels.css?v=20261101d")

    def test_switch_store_link_only_when_a_second_shop_exists(self):
        self.assertNotContains(self.client.get(reverse("dashboard")), "Сменить магазин")
        GuardAccount.objects.create(
            user=self.seller, marketplace="wb", name="Second WB",
            initial_sync_completed_at=timezone.now(), token_ciphertext="opaque2",
        )
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Сменить магазин")
        self.assertContains(response, 'href="/stores/"')

    @override_settings(NOSALE_WB_FINANCE_ENABLED=False)
    def test_navigation_only_exposes_available_data_for_selected_store(self):
        home = self.client.get(reverse("dashboard"))
        self.assertNotContains(home, 'href="/promotions/"')
        self.assertNotContains(home, 'href="/finance/"')
        self.assertNotContains(home, "<strong>Магазины</strong>")
        self.assertContains(self.client.get(reverse("settings")), "Загрузить календарь акций WB")

        PromotionSnapshot.objects.create(
            account=self.shop, promotions=[{
                "id": 15, "name": "Календарь WB", "type": "auto",
                "starts_at": "2026-11-01T00:00:00Z",
                "ends_at": "2026-11-11T00:00:00Z",
                "risk": "unknown", "participation": None,
            }], fetched_at=timezone.now(), partial=True,
        )
        home = self.client.get(reverse("dashboard"))
        self.assertContains(home, 'href="/promotions/"', count=1)
        self.assertNotContains(home, 'href="/finance/"')
        calendar = self.client.get(reverse("promotion_audit"))
        self.assertContains(calendar, 'Календарь Wildberries')
        self.assertContains(calendar, 'Календарь WB')
        self.assertNotContains(calendar, 'Что дальше')

        other = GuardAccount.objects.create(
            user=self.seller, marketplace="wb", name="Secondary WB",
            initial_sync_completed_at=timezone.now(), token_ciphertext="opaque2",
        )
        session = self.client.session
        session["mpno_selected_store_id"] = other.pk
        session.save()
        second_home = self.client.get(reverse("dashboard"))
        self.assertNotContains(second_home, 'href="/promotions/"')
        self.assertContains(second_home, "<strong>Магазины</strong>")
        self.assertContains(second_home, 'href="/stores/"')
        session = self.client.session
        session["mpno_selected_store_id"] = self.shop.pk
        session.save()
        self.assertContains(self.client.get(reverse("dashboard")), 'href="/promotions/"')

    def test_notifications_show_actual_messages_in_compact_grid(self):
        PushMessage.objects.create(user=self.seller, kind="digest", title="Отчёт QA",
                                   body="Без изменений")
        page = self.client.get(reverse("notifications"))
        self.assertContains(page, 'compact-feed-grid')
        self.assertContains(page, 'Отчёт QA')
        self.assertNotContains(page, "Пока нет уведомлений.")
        self.assertNotContains(page, "Доставка уведомлений")

    def test_shared_wide_workspace_only_on_authenticated_seller_pages(self):
        for route in ("dashboard", "settings", "notifications", "promotion_audit",
                      "stores", "history", "subscription"):
            with self.subTest(route=route):
                page = self.client.get(reverse(route))
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, "seller-workspace-page")
                self.assertContains(page, "seller-dashboard-grid.css?v=20261031b")
                self.assertContains(page, "seller-workspace-panels.css?v=20261101d")
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, "seller-dashboard-page")
        notifications = self.client.get(reverse("notifications"))
        self.assertNotContains(notifications, "seller-dashboard-page")
        self.client.logout()
        public = self.client.get(reverse("login"))
        self.assertNotContains(public, "seller-workspace-page")
        self.assertNotContains(public, "seller-dashboard-grid.css")
        self.assertNotContains(public, "seller-workspace-panels.css")

    def test_notifications_are_feed_only_settings_own_configuration(self):
        feed = self.client.get(reverse("notifications"))
        self.assertContains(feed, "<h1>Уведомления</h1>")
        self.assertContains(feed, "compact-inbox")
        self.assertContains(feed, "Пока нет уведомлений.")
        self.assertNotContains(feed, "Доставка уведомлений")
        self.assertNotContains(feed, "Важные события")
        self.assertNotContains(feed, 'name="timezone_name"')
        self.assertNotContains(feed, 'action="/push/preferences/"')
        config = self.client.get(reverse("settings"))
        self.assertContains(config, "workspace-settings-grid")
        self.assertContains(config, "compact-settings-grid")
        self.assertContains(config, "compact-prefs-card")
        self.assertContains(self.client.get(reverse("stores")), "workspace-store-row")
        self.assertContains(self.client.get(reverse("promotion_audit")), "Обновить календарь")
        self.assertContains(self.client.get(reverse("subscription")), "workspace-tariff-cards")
        self.assertContains(self.client.get(reverse("history")), "workspace-panel-grid")

        self.assertNotContains(feed, 'form action="/push/preferences/"')
        self.assertNotContains(feed, 'action="/push/preferences/"')
        self.assertNotContains(feed, 'id="push-enable"')
        config = self.client.get(reverse("settings"))
        self.assertContains(config, 'id="notification-settings"')
        self.assertContains(config, 'action="/push/preferences/"')
        self.assertContains(config, 'name="instant_enabled"')
        self.assertContains(config, 'name="timezone_name"')
        posted = self.client.post(reverse("push_preferences"), {
            "instant_enabled": "on",
            "daily_enabled": "on",
            "timezone_name": "Europe/Moscow",
        })
        self.assertRedirects(posted, reverse("settings"), fetch_redirect_response=False)
        pref = NotificationPreference.objects.get(user=self.seller)
        self.assertTrue(pref.instant_enabled)
        self.assertTrue(pref.daily_enabled)
        self.assertContains(self.client.get(reverse("settings")), 'name="instant_enabled" checked')

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
        self.assertContains(response, "Уведомления")
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
