"""Read receipts are POST-only, viewport-scoped, and tenant-safe."""
import json
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from push.models import PushMessage
from .models import GuardAccount, GuardEvent
from .notifications_feed import unread_notification_groups


@override_settings(SAAS_WB_NETWORK_ENABLED=False, SAAS_ACCESS_ENFORCED=False, SECURE_SSL_REDIRECT=False)
class ViewedNotificationReceiptTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.seller = User.objects.create_user(
            username="seller-read@testing.invalid", email="seller-read@testing.invalid")
        self.other = User.objects.create_user(
            username="other-read@testing.invalid", email="other-read@testing.invalid")
        self.store = GuardAccount.objects.create(user=self.seller, name="Магазин WB")
        self.other_store = GuardAccount.objects.create(user=self.other, name="Другая компания")
        self.client.force_login(self.seller)
        self.url = reverse("mark_notifications_viewed")

    def post_receipts(self, events=(), reports=()):
        return self.client.post(
            self.url, data=json.dumps({"events": list(events), "reports": list(reports)}),
            content_type="application/json",
        )

    def test_get_displays_unread_without_mutating_database(self):
        e = GuardEvent.objects.create(account=self.store, kind="error", message="WB 429")
        report = PushMessage.objects.create(user=self.seller, kind="digest", title="Digest")
        page = self.client.get(reverse("notifications"), {"type": "errors"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'data-notice-id="{e.pk}"')
        self.assertContains(page, 'seller-notifications.js?v=20261110a')
        self.assertEqual(unread_notification_groups(self.seller), 2)
        e.refresh_from_db()
        report.refresh_from_db()
        self.assertIsNone(e.read_at)
        self.assertIsNone(report.read_at)

    def test_viewed_error_marks_only_that_group_and_updates_counter(self):
        first = GuardEvent.objects.create(account=self.store, kind="error", message="WB HTTP 429")
        second = GuardEvent.objects.create(account=self.store, kind="error", message="WB HTTP 429")
        different = GuardEvent.objects.create(account=self.store, kind="error", message="WB HTTP 500")
        report = PushMessage.objects.create(user=self.seller, kind="digest", title="Report")
        result = self.post_receipts(events=[second.pk])
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["unread"], 2)
        for pk in (first.pk, second.pk):
            self.assertIsNotNone(GuardEvent.objects.get(pk=pk).read_at)
        different.refresh_from_db()
        report.refresh_from_db()
        self.assertIsNone(different.read_at)
        self.assertIsNone(report.read_at)

    def test_later_identical_errors_remain_unread(self):
        before = GuardEvent.objects.create(account=self.store, kind="error", message="WB 429")
        anchor = GuardEvent.objects.create(account=self.store, kind="error", message="WB 429")
        later = GuardEvent.objects.create(account=self.store, kind="error", message="WB 429")
        result = self.post_receipts(events=[anchor.pk])
        self.assertEqual(result.json()["unread"], 1)
        before.refresh_from_db()
        anchor.refresh_from_db()
        later.refresh_from_db()
        self.assertIsNotNone(before.read_at)
        self.assertIsNotNone(anchor.read_at)
        self.assertIsNone(later.read_at)

    def test_report_receipt_does_not_mark_other_reports(self):
        a = PushMessage.objects.create(user=self.seller, kind="digest", title="A")
        b = PushMessage.objects.create(user=self.seller, kind="smart", title="B")
        result = self.post_receipts(reports=[a.pk])
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["unread"], 1)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertIsNotNone(a.read_at)
        self.assertIsNone(b.read_at)

    def test_cross_tenant_ids_are_not_marked(self):
        own = GuardEvent.objects.create(account=self.store, kind="error", message="Error")
        foreign = GuardEvent.objects.create(account=self.other_store, kind="error", message="Error")
        foreign_report = PushMessage.objects.create(user=self.other, kind="digest", title="Secret")
        result = self.post_receipts(events=[own.pk, foreign.pk], reports=[foreign_report.pk])
        self.assertEqual(result.status_code, 200)
        own.refresh_from_db()
        foreign.refresh_from_db()
        foreign_report.refresh_from_db()
        self.assertIsNotNone(own.read_at)
        self.assertIsNone(foreign.read_at)
        self.assertIsNone(foreign_report.read_at)
        self.assertEqual(result.json()["unread"], 0)

    def test_read_request_requires_post_csrf_and_authenticated_account(self):
        e = GuardEvent.objects.create(account=self.store, kind="error", message="Problem")
        self.assertEqual(self.client.get(self.url).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.seller)
        self.assertEqual(
            strict.post(self.url, data=json.dumps({"events": [e.pk]}),
                        content_type="application/json").status_code, 403)
        self.client.logout()
        self.assertEqual(self.post_receipts(events=[e.pk]).status_code, 302)
        e.refresh_from_db()
        self.assertIsNone(e.read_at)

    def test_input_validation_and_bounded_batch(self):
        e = GuardEvent.objects.create(account=self.store, kind="error", message="Problem")
        for payload in (
            {"events": ["1"]}, {"events": [True]},
            {"events": [0]}, {"events": [-4]}, {"events": list(range(1, 30))},
            {"events": "wrong"}, [e.pk], {"reports": [2**70]},
        ):
            response = self.client.post(
                self.url, data=json.dumps(payload), content_type="application/json")
            self.assertEqual(response.status_code, 400, payload)
        e.refresh_from_db()
        self.assertIsNone(e.read_at)

    def test_only_current_page_rows_are_marked(self):
        events = [
            GuardEvent.objects.create(
                account=self.store, kind="error", message=f"Problem {idx}",
                created_at=timezone.now()+timedelta(seconds=idx),
            )
            for idx in range(18)
        ]
        page = self.client.get(reverse("notifications"), {"type": "errors", "page": 1})
        self.assertEqual(len(page.context["notification_items"]), 12)
        visible = [row["key"] for row in page.context["notification_items"]]
        result = self.post_receipts(events=visible)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["unread"], 6)
        self.assertEqual(
            GuardEvent.objects.filter(pk__in=[e.pk for e in events], read_at__isnull=True).count(), 6)
