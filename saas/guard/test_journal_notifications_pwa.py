import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import GuardAccount, GuardEvent, Upload


@override_settings(SAAS_WB_NETWORK_ENABLED=False, SAAS_ACCESS_ENFORCED=True)
class JournalNotificationsPWATests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice@events.test", "alice@events.test", "safe-pass-888")
        self.bob = User.objects.create_user("bob@events.test", "bob@events.test", "safe-pass-888")
        self.shop_a = GuardAccount.objects.create(user=self.alice, name="WB One")
        self.shop_b = GuardAccount.objects.create(user=self.alice, name="WB Two")
        self.other_shop = GuardAccount.objects.create(user=self.bob, name="Bob WB")
        self.client.force_login(self.alice)

    def test_routine_checks_are_aggregated_instead_of_rendered_as_rows(self):
        for _ in range(4):
            GuardEvent.objects.create(account=self.shop_a, kind="check", message="Техническая проверка")
        GuardEvent.objects.create(account=self.shop_a, kind="submitted", message="Отправлено WB")
        response = self.client.get(reverse("history"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Проверок: 4")
        self.assertNotContains(response, "Техническая проверка")
        self.assertContains(response, "Отправлено WB")

    def test_confirmed_corrections_show_exact_product_details(self):
        changes = [
            {"nm_id": 123, "vendor_code": "A-123", "before": 25, "after": 10},
            {"nm_id": 456, "vendor_code": "B-456", "before": 40, "after": 15},
        ]
        Upload.objects.create(account=self.shop_a, upload_id=9001, product_ids=[123,456],
                              corrected_count=2, change_details=changes, status="success")
        GuardEvent.objects.create(account=self.shop_a, kind="confirmed",
                                  message="Подтверждено WB",
                                  details={"upload_id":9001,"changes":changes})
        response = self.client.get(reverse("history"))
        self.assertContains(response, "A-123")
        self.assertContains(response, "B-456")
        self.assertContains(response, "25%")
        self.assertContains(response, "10%")
        self.assertContains(response, "Подтверждено WB")

    def test_notifications_include_only_important_events_from_my_stores(self):
        GuardEvent.objects.create(account=self.shop_a, kind="check", message="check")
        GuardEvent.objects.create(account=self.shop_a, kind="confirmed", message="Alice confirmed")
        GuardEvent.objects.create(account=self.shop_b, kind="upload_error", message="Alice failure")
        GuardEvent.objects.create(account=self.other_shop, kind="confirmed", message="Bob secret")
        inbox = self.client.get(reverse("notifications"))
        self.assertContains(inbox, "Alice confirmed")
        self.assertContains(inbox, "Alice failure")
        self.assertNotContains(inbox, "Bob secret")
        self.assertNotIn("check", [event.kind for event in inbox.context["events"]])
        self.assertContains(inbox, "Новое", count=2)

    def test_read_action_is_post_only_and_does_not_mark_others(self):
        mine = GuardEvent.objects.create(account=self.shop_a, kind="confirmed", message="Mine")
        theirs = GuardEvent.objects.create(account=self.other_shop, kind="confirmed", message="Theirs")
        self.assertEqual(self.client.get(reverse("mark_notifications_read")).status_code, 405)
        self.assertEqual(self.client.post(reverse("mark_notifications_read")).status_code, 302)
        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertIsNotNone(mine.read_at)
        self.assertIsNone(theirs.read_at)

    def test_csrf_protects_notification_mutation(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.alice)
        self.assertEqual(strict.post(reverse("mark_notifications_read")).status_code, 403)

    def test_journal_does_not_cross_store_boundaries(self):
        GuardEvent.objects.create(account=self.shop_a, kind="confirmed", message="Shop one")
        GuardEvent.objects.create(account=self.shop_b, kind="confirmed", message="Shop two")
        GuardEvent.objects.create(account=self.other_shop, kind="confirmed", message="Foreign")
        inbox = self.client.get(reverse("notifications"))
        self.assertContains(inbox,"Shop one")
        self.assertContains(inbox,"Shop two")
        self.assertNotContains(inbox,"Foreign")
        history = self.client.get(reverse("history"))
        self.assertContains(history,"Shop one")
        self.assertNotContains(history,"Shop two")
        self.assertNotContains(history,"Foreign")

    def test_pwa_manifest_is_public_and_worker_is_safe(self):
        self.client.logout()
        manifest_response = self.client.get(reverse("pwa_manifest"))
        self.assertEqual(manifest_response.status_code, 200)
        manifest = json.loads(b"".join(manifest_response.streaming_content))
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/")
        worker_response = self.client.get(reverse("pwa_service_worker"))
        self.assertEqual(worker_response.status_code, 200)
        self.assertEqual(worker_response["Service-Worker-Allowed"], "/")
        worker = b"".join(worker_response.streaming_content).decode("utf-8")
        self.assertIn('SAFE_PATHS = ["/favicon.svg"]', worker)
        self.assertNotIn("caches.open('/api", worker)
