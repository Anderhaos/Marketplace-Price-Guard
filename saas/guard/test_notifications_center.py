"""Notification center regression: one incident, no 4-column error spam."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from push.models import PushMessage
from .models import GuardAccount, GuardEvent
from .notifications_feed import unread_notification_groups


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SAAS_PIN_AUTH_ENABLED=False,
    SAAS_ACCESS_ENFORCED=False,
)
class GroupedSellerNotificationsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.seller = User.objects.create_user(
            username="seller-inbox@test.invalid", email="seller-inbox@test.invalid",
        )
        self.other = User.objects.create_user(
            username="foreign-inbox@test.invalid", email="foreign-inbox@test.invalid",
        )
        self.store = GuardAccount.objects.create(user=self.seller, name="Мой WB")
        self.foreign_store = GuardAccount.objects.create(user=self.other, name="Чужой магазин")
        self.client.force_login(self.seller)

    def test_forty_two_repeated_429_events_become_one_row_and_badge(self):
        for i in range(42):
            GuardEvent.objects.create(
                account=self.store, kind="error",
                created_at=timezone.now()-timedelta(hours=42-i),
                message="Wildberries ограничил частоту запросов (HTTP 429). NoSale повторит.",
            )
        page = self.client.get(reverse("notifications"))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["notification_counts"]["errors"], 1)
        self.assertEqual(page.context["unread_notification_count"], 1)
        self.assertEqual(len(page.context["notification_items"]), 1)
        self.assertContains(page, "Ограничение запросов Wildberries", count=1)
        self.assertContains(page, "Повторений: 42", count=1)
        self.assertNotContains(page, "balanced-feed-grid")
        self.assertContains(page, 'class="notice-list"')
        self.assertEqual(GuardEvent.objects.filter(account=self.store).count(), 42)

    def test_tenant_boundary_and_separate_problem_messages(self):
        GuardEvent.objects.create(
            account=self.store,kind="error",message="Unexpected HTTP 503",
        )
        GuardEvent.objects.create(
            account=self.store,kind="error",message="Unexpected HTTP 500",
        )
        GuardEvent.objects.create(
            account=self.foreign_store,kind="error",message="Foreign private error",
        )
        inbox=self.client.get(reverse("notifications"), {"type": "errors"})
        self.assertEqual(inbox.context["notification_counts"]["errors"], 2)
        self.assertContains(inbox, "Unexpected HTTP 503")
        self.assertContains(inbox, "Unexpected HTTP 500")
        self.assertNotContains(inbox, "Foreign private error")
        self.assertEqual(unread_notification_groups(self.seller), 2)

    def test_confirmed_changes_collapsed_and_reports_visible(self):
        changes=[{"nm_id":100+i,"before":20,"after":13} for i in range(20)]
        GuardEvent.objects.create(
            account=self.store,kind="confirmed",
            message="Изменено 20 товаров",details={"changes":changes},
        )
        PushMessage.objects.create(
            user=self.seller,kind="digest",
            title="Отчёт за сегодня",body="20 исправлений",
        )
        page=self.client.get(reverse("notifications"))
        self.assertContains(page, "Показать товары (20)")
        self.assertContains(page, '<details class="notice-details">', count=1)
        self.assertContains(page, "Отчёт за сегодня")
        self.assertEqual(page.context["unread_notification_count"], 2)
        report=self.client.get(reverse("notifications"),{"type":"reports"})
        self.assertContains(report,"Отчёт за сегодня")
        self.assertNotContains(report,"Изменено 20 товаров")
        success=self.client.get(reverse("notifications"),{"type":"changes"})
        self.assertContains(success,"Показать товары (20)")
        self.assertNotContains(success,"Отчёт за сегодня")

    def test_mark_read_updates_badge_and_keeps_audit(self):
        originals=[
            GuardEvent.objects.create(account=self.store,kind="error",message="WB HTTP 429")
            for _ in range(6)
        ]
        foreign=GuardEvent.objects.create(
            account=self.foreign_store,kind="error",message="WB HTTP 429"
        )
        self.assertEqual(unread_notification_groups(self.seller),1)
        response=self.client.post(reverse("mark_notifications_read"))
        self.assertEqual(response.status_code,302)
        self.assertEqual(unread_notification_groups(self.seller),0)
        self.assertTrue(all(GuardEvent.objects.get(pk=e.pk).read_at for e in originals))
        foreign.refresh_from_db()
        self.assertIsNone(foreign.read_at)
        self.assertEqual(GuardEvent.objects.filter(account=self.store).count(),6)

    def test_pagination_and_invalid_query_are_bounded(self):
        for i in range(28):
            GuardEvent.objects.create(account=self.store,kind="error",message=f"Error {i}")
        first=self.client.get(reverse("notifications"))
        self.assertEqual(len(first.context["notification_items"]),12)
        self.assertEqual(first.context["notification_items"].paginator.num_pages,3)
        next_page=self.client.get(reverse("notifications"),{"page":"2","type":"errors"})
        self.assertEqual(len(next_page.context["notification_items"]),12)
        invalid=self.client.get(reverse("notifications"),{"page":"bad","type":"unknown"})
        self.assertEqual(invalid.status_code,200)
        self.assertEqual(invalid.context["notification_filter"],"all")

    def test_previous_errors_are_labelled_without_false_active_alarm(self):
        previous=timezone.now()-timedelta(days=4)
        self.store.last_check_at=timezone.now()-timedelta(hours=1)
        self.store.last_error=""
        self.store.save(update_fields=["last_check_at","last_error"])
        GuardEvent.objects.create(
            account=self.store,kind="error",message="WB HTTP 429",
            created_at=previous,
        )
        response=self.client.get(reverse("notifications"))
        self.assertContains(response,"Было ранее")
        self.assertNotContains(response,"Требует внимания")
