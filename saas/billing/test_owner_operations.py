"""Global owner-only worker policy: real runtime effects, UI security, audit."""
import base64
from datetime import timedelta
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant, OwnerOperationsSettings
from guard.models import GuardAccount, GuardEvent
from guard.monitor import run_due_checks
from push.models import PushDelivery, PushMessage, PushSubscription
from push.service import queue_new_events, create_daily_digests, deliver_due_pushes


@override_settings(
    SAAS_PIN_AUTH_ENABLED=False,
    SAAS_WB_NETWORK_ENABLED=True,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class OwnerOperationsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="global-worker-owner@example.test",
            email="global-worker-owner@example.test", password="testpass",
        )
        self.seller = User.objects.create_user(
            username="global-worker-seller@example.test", password="testpass",
        )
        self.staff = User.objects.create_user(
            username="global-worker-staff@example.test", password="testpass", is_staff=True,
        )
        self.policy = OwnerOperationsSettings.runtime()
        self.assertIsNotNone(self.policy)

    def test_owner_can_edit_admin_and_audit_without_customer_permissions(self):
        url = reverse("admin:billing_owneroperationssettings_change", args=[1])
        self.client.force_login(self.owner)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Новые фоновые проверки WB")
        fields = {
            "instant_events_enabled": "on",
            "daily_digests_enabled": "on",
            "push_delivery_enabled": "on",
            "guard_poll_seconds": "12",
            "notifications_poll_seconds": "37",
            "push_batch_size": "3",
            "_save": "Сохранить",
        }
        self.assertEqual(self.client.post(url, fields).status_code, 302)
        self.policy.refresh_from_db()
        self.assertFalse(self.policy.guard_checks_enabled)
        self.assertEqual(self.policy.guard_poll_seconds, 12)
        self.assertEqual(self.policy.push_batch_size, 3)
        self.assertTrue(LogEntry.objects.filter(
            user=self.owner, content_type__model="owneroperationssettings",
            action_flag=2,
        ).exists())
        self.assertEqual(
            self.client.get(reverse("admin:billing_owneroperationssettings_delete", args=[1])).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse("admin:billing_owneroperationssettings_add")).status_code,
            403,
        )
        self.assertContains(self.client.get(reverse("admin:admin_logentry_changelist")),
                            "NoSale")
        for user in (self.staff, self.seller):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(
                self.client.get(reverse("admin:admin_logentry_changelist")).status_code,
                403,
            )

    def test_guard_pause_blocks_all_new_wb_cycles_without_token_access(self):
        shop = GuardAccount.objects.create(
            user=self.seller, name="Seller WB",
            token_ciphertext="synthetic-not-decryptable", next_check_at=timezone.now()-timedelta(hours=1),
            enabled=True,
        )
        AccessGrant.objects.create(
            user=self.seller, source="admin", source_ref="worker-global-test",
            plan="standard", starts_at=timezone.now()-timedelta(minutes=1),
            ends_at=timezone.now()+timedelta(hours=2),
        )
        self.policy.guard_checks_enabled = False
        self.policy.save(update_fields=["guard_checks_enabled"])
        with patch("guard.monitor.decrypt_token") as decrypt, patch("guard.monitor.check_account") as check:
            self.assertEqual(run_due_checks(), 0)
            decrypt.assert_not_called()
            check.assert_not_called()
        shop.refresh_from_db()
        self.assertIsNone(shop.lock_until)

    def test_instant_push_pauses_enqueue_but_still_keeps_event_feed(self):
        shop = GuardAccount.objects.create(user=self.seller, name="Seller WB")
        event = GuardEvent.objects.create(account=shop, kind="error", message="Test offline")
        self.policy.instant_events_enabled = False
        self.policy.save(update_fields=["instant_events_enabled"])
        with patch("push.service._enqueue_for_devices") as push:
            self.assertEqual(queue_new_events(), 1)
            push.assert_not_called()
        self.assertTrue(PushMessage.objects.filter(event=event).exists())
        self.assertEqual(PushDelivery.objects.count(), 0)

    def test_daily_digest_pause_does_not_generate_messages(self):
        self.policy.daily_digests_enabled = False
        self.policy.save(update_fields=["daily_digests_enabled"])
        with patch("push.service._highest_priority_smart_advice") as ai:
            self.assertEqual(create_daily_digests(), 0)
            ai.assert_not_called()
        self.assertEqual(PushMessage.objects.count(), 0)

    @override_settings(PUSH_VAPID_PRIVATE_KEY="fake", PUSH_VAPID_PUBLIC_KEY="fake")
    def test_push_delivery_pause_and_batch_limit_preserve_pending_queue(self):
        endpoint = "https://fcm.googleapis.com/fcm/send/owner-global-safe"
        sub = PushSubscription.objects.create(
            user=self.seller, endpoint=endpoint,
            p256dh=base64.urlsafe_b64encode(bytes(range(65))).decode().rstrip("="),
            auth=base64.urlsafe_b64encode(bytes(range(16))).decode().rstrip("="),
        )
        now = timezone.now()
        for idx in range(4):
            message = PushMessage.objects.create(
                user=self.seller, kind="event", title="Test", body="Test",
            )
            PushDelivery.objects.create(
                message=message, subscription=sub, state="pending",
                next_attempt_at=now-timedelta(seconds=1),
            )
        self.policy.push_delivery_enabled = False
        self.policy.save(update_fields=["push_delivery_enabled"])
        with patch("push.service.validate_endpoint") as check:
            self.assertEqual(deliver_due_pushes(now=now, send=lambda **kw: None), 0)
            check.assert_not_called()
        self.assertEqual(PushDelivery.objects.filter(state="pending").count(), 4)
        self.policy.push_delivery_enabled = True
        self.policy.push_batch_size = 2
        self.policy.save(update_fields=["push_delivery_enabled", "push_batch_size"])
        fake_send = __import__("unittest.mock", fromlist=["Mock"]).Mock()
        self.assertEqual(deliver_due_pushes(now=now, send=fake_send), 2)
        self.assertEqual(fake_send.call_count, 2)
        self.assertEqual(PushDelivery.objects.filter(state="pending").count(), 2)
        self.assertEqual(PushDelivery.objects.filter(state="sent").count(), 2)

    def test_live_worker_sleep_bounds_from_owner_database(self):
        self.policy.guard_poll_seconds = 17
        self.policy.notifications_poll_seconds = 63
        self.policy.save(update_fields=["guard_poll_seconds", "notifications_poll_seconds"])
        # The real management command deliberately closes old connections;
        # patch that lifecycle hook in this TestCase's atomic transaction,
        # otherwise PostgreSQL loses its test connection for subsequent cases.
        with patch("guard.management.commands.run_guard.close_old_connections"), \
             patch("guard.management.commands.run_guard.run_due_checks", return_value=0), \
             patch("guard.management.commands.run_guard.time.sleep", side_effect=RuntimeError("stop")) as sleep:
            with self.assertRaisesRegex(RuntimeError, "stop"):
                call_command("run_guard")
            sleep.assert_called_once_with(17)
        with patch("push.management.commands.run_notifications.close_old_connections"), \
             patch("push.management.commands.run_notifications.process_notification_cycle", return_value={}), \
             patch("push.management.commands.run_notifications.time.sleep", side_effect=RuntimeError("stop")) as sleep:
            with self.assertRaisesRegex(RuntimeError, "stop"):
                call_command("run_notifications")
            sleep.assert_called_once_with(63)

    def test_readiness_owner_page_is_local_and_includes_operational_truth(self):
        self.policy.guard_checks_enabled = False
        self.policy.push_delivery_enabled = False
        self.policy.save(update_fields=["guard_checks_enabled", "push_delivery_enabled"])
        self.client.force_login(self.owner)
        r = self.client.get(reverse("nosale_owner_integrations"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Приостановлен")
        self.assertContains(r, reverse("admin:billing_owneroperationssettings_changelist"))
        self.assertNotContains(r, "synthetic-not-decryptable")
        for user in (self.staff, self.seller):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("nosale_owner_integrations")).status_code, 403)
