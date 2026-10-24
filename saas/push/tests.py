import base64
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant
from guard.models import GuardAccount, GuardEvent, Product
from push.models import NotificationPreference, PushDelivery, PushMessage, PushSubscription
from push.service import (
    create_daily_digests, deliver_due_pushes, queue_new_events,
    register_subscription, validate_endpoint,
)


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def subscription_data(endpoint="https://fcm.googleapis.com/fcm/send/abc123"):
    return {
        "endpoint": endpoint,
        "keys": {"p256dh": b64(b"\x04" + b"\x02" * 64), "auth": b64(b"\x03" * 16)},
    }


@override_settings(
    PUSH_VAPID_PUBLIC_KEY="testing-public",
    PUSH_VAPID_PRIVATE_KEY="testing-private",
    PUSH_VAPID_SUBJECT="mailto:testing@example.com",
    SAAS_WB_NETWORK_ENABLED=False,
)
class PushTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice@push.test", "alice@push.test", "pass-very-strong-999")
        self.bob = User.objects.create_user("bob@push.test", "bob@push.test", "pass-very-strong-999")
        self.shop = GuardAccount.objects.create(user=self.alice, name="Alice WB")
        self.foreign_shop = GuardAccount.objects.create(user=self.bob, name="Bob WB")
        self.client.force_login(self.alice)

    def subscribe(self, client=None, endpoint=None):
        data = subscription_data(endpoint or "https://fcm.googleapis.com/fcm/send/abc123")
        response = (client or self.client).post(
            reverse("push_subscribe"), data=json.dumps(data), content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        return PushSubscription.objects.get(endpoint=data["endpoint"])

    def test_subscription_requires_authenticated_post(self):
        self.assertEqual(self.client.get(reverse("push_subscribe")).status_code, 405)
        self.client.logout()
        self.assertEqual(
            self.client.post(reverse("push_subscribe"), "{}", content_type="application/json").status_code, 302,
        )

    def test_json_subscription_is_strict_and_csrf_protected(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.alice)
        response = strict.post(
            reverse("push_subscribe"), data=json.dumps(subscription_data()), content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(PushSubscription.objects.count(), 0)

    def test_endpoint_rejects_localhost_and_untrusted_domains(self):
        for endpoint in [
            "http://fcm.googleapis.com/send/a",
            "https://127.0.0.1:443/private",
            "https://fcm.googleapis.com.evil.test/fcm/send/a",
            "https://localhost:443/",
            "https://user:pass@fcm.googleapis.com/",
            "https://fcm.googleapis.com:444/send",
            "https://fcm.googleapis.com:invalid/send",
        ]:
            with self.subTest(endpoint=endpoint):
                response = self.client.post(
                    reverse("push_subscribe"), data=json.dumps(subscription_data(endpoint)),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(PushSubscription.objects.count(), 0)

    def test_valid_registration_and_user_opt_out(self):
        sub = self.subscribe()
        self.assertEqual(sub.user, self.alice)
        response = self.client.post(
            reverse("push_unsubscribe"),
            data=json.dumps({"endpoint": sub.endpoint}), content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        sub.refresh_from_db()
        self.assertFalse(sub.active)

    def test_shared_device_is_reassigned_without_old_queued_deliveries(self):
        sub = self.subscribe()
        event = GuardEvent.objects.create(account=self.shop, kind="confirmed", message="Alice fixed")
        queue_new_events()
        delivery = PushDelivery.objects.get(message__event=event)
        self.assertEqual(delivery.state, "pending")
        self.client.force_login(self.bob)
        new_sub = self.subscribe()
        self.assertEqual(new_sub.pk, sub.pk)
        self.assertEqual(new_sub.user, self.bob)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "gone")

    def test_explicit_logout_disables_registered_device(self):
        sub = self.subscribe()
        self.client.post(reverse("logout"))
        sub.refresh_from_db()
        self.assertFalse(sub.active)

    def test_notify_only_after_confirmed_event_and_never_for_other_account(self):
        sub = self.subscribe()
        GuardEvent.objects.create(account=self.shop, kind="submitted", message="Not yet confirmed")
        confirmed = GuardEvent.objects.create(
            account=self.shop, kind="confirmed", message="Yes",
            details={"changes": [{"nm_id": 112, "before": 30, "after": 10}]},
        )
        foreign = GuardEvent.objects.create(account=self.foreign_shop, kind="error", message="Private")
        self.assertEqual(queue_new_events(), 2)
        self.assertEqual(queue_new_events(), 0)
        self.assertFalse(PushMessage.objects.filter(event__kind="submitted").exists())
        self.assertEqual(PushDelivery.objects.filter(message__event=confirmed, subscription=sub).count(), 1)
        self.assertFalse(PushDelivery.objects.filter(message__event=foreign).exists())
        self.assertEqual(PushMessage.objects.filter(event=confirmed).count(), 1)

    def test_event_from_before_device_registration_is_not_sent(self):
        event = GuardEvent.objects.create(
            account=self.shop, kind="confirmed", message="Past confirmation",
            created_at=timezone.now() - timedelta(hours=3),
        )
        self.subscribe()
        self.assertEqual(queue_new_events(), 1)
        self.assertFalse(PushDelivery.objects.filter(message__event=event).exists())

    def test_opt_out_instant_suppresses_push_but_keeps_in_app_event(self):
        self.subscribe()
        NotificationPreference.objects.create(user=self.alice, instant_enabled=False)
        event = GuardEvent.objects.create(account=self.shop, kind="error", message="Error still visible")
        queue_new_events()
        self.assertTrue(PushMessage.objects.filter(event=event).exists())
        self.assertFalse(PushDelivery.objects.filter(message__event=event).exists())

    def test_mock_delivery_succeeds_once(self):
        self.subscribe()
        event = GuardEvent.objects.create(account=self.shop, kind="confirmed", message="Confirmed")
        queue_new_events()
        sender = Mock()
        self.assertEqual(deliver_due_pushes(send=sender), 1)
        sender.assert_called_once()
        self.assertEqual(sender.call_args.kwargs["data"].count("Подтверд"), 0)
        assert json.loads(sender.call_args.kwargs["data"])["url"] == "/notifications/"
        delivery = PushDelivery.objects.get(message__event=event)
        self.assertEqual(delivery.state, "sent")
        self.assertEqual(deliver_due_pushes(send=sender), 0)

    def test_push_delivery_routes_only_allowlisted_internal_pages(self):
        sub = self.subscribe()
        for path, expected in (
            ("/advisor/", "/advisor/"),
            ("/stores/", "/stores/"),
            ("https://evil.example/steal", "/notifications/"),
        ):
            with self.subTest(path=path):
                notice = PushMessage.objects.create(
                    user=self.alice, kind="event", title="Test", body="Action", url=path,
                )
                PushDelivery.objects.create(message=notice, subscription=sub)
                sender = Mock()
                self.assertEqual(deliver_due_pushes(send=sender), 1)
                self.assertEqual(json.loads(sender.call_args.kwargs["data"])["url"], expected)

    def test_gone_delivery_disables_subscription(self):
        sub = self.subscribe()
        event = GuardEvent.objects.create(account=self.shop, kind="confirmed", message="Confirmed")
        queue_new_events()
        failure = Exception("Gone")
        failure.status_code = 410
        sender = Mock(side_effect=failure)
        self.assertEqual(deliver_due_pushes(send=sender), 1)
        sub.refresh_from_db()
        self.assertFalse(sub.active)
        self.assertEqual(PushDelivery.objects.get(message__event=event).state, "gone")

    def test_transient_failure_retries_later_without_blocking(self):
        self.subscribe()
        event = GuardEvent.objects.create(account=self.shop, kind="error", message="Error")
        queue_new_events()
        sender = Mock(side_effect=TimeoutError("provider timeout"))
        now = timezone.now()
        self.assertEqual(deliver_due_pushes(now=now, send=sender), 1)
        delivery = PushDelivery.objects.get(message__event=event)
        self.assertEqual(delivery.state, "pending")
        self.assertEqual(delivery.attempts, 1)
        self.assertGreater(delivery.next_attempt_at, now)
        self.assertEqual(deliver_due_pushes(now=now, send=sender), 0)

    def test_moscow_digest_at_20_not_twice(self):
        self.subscribe()
        Product.objects.create(account=self.shop, nm_id=101, active=True)
        NotificationPreference.objects.create(user=self.alice, timezone_name="Europe/Moscow")
        NotificationPreference.objects.create(user=self.bob, daily_enabled=False)
        local = datetime(2026, 10, 22, 20, 1, tzinfo=ZoneInfo("Europe/Moscow"))
        now = local.astimezone(dt_timezone.utc)
        GuardEvent.objects.create(account=self.shop, kind="check", message="checked", created_at=now - timedelta(hours=1))
        GuardEvent.objects.create(
            account=self.shop, kind="confirmed", message="confirmed",
            created_at=now - timedelta(hours=1),
            details={"changes": [{"nm_id": 101}, {"nm_id": 102}]},
        )
        AccessGrant.objects.create(
            user=self.alice, plan="standard", source="admin",
            source_ref="push-digest-alice-test",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=1),
        )
        self.assertEqual(create_daily_digests(now=now), 1)
        self.assertEqual(create_daily_digests(now=now + timedelta(minutes=1)), 0)
        digest = PushMessage.objects.get(user=self.alice, kind="digest")
        self.assertEqual(digest.digest_date.isoformat(), "2026-10-22")
        self.assertIn("исправлений: 2", digest.body)
        self.assertIn("проверок: 1", digest.body)
        self.assertEqual(digest.deliveries.count(), 1)
        self.assertFalse(PushMessage.objects.filter(user=self.bob, kind="digest").exists())

    def test_before_evening_cutoff_creates_no_digest(self):
        self.subscribe()
        pref = NotificationPreference.objects.create(user=self.alice, timezone_name="Europe/Moscow")
        NotificationPreference.objects.create(user=self.bob, daily_enabled=False)
        now = datetime(2026, 10, 22, 19, 59, tzinfo=ZoneInfo("Europe/Moscow")).astimezone(dt_timezone.utc)
        self.assertEqual(create_daily_digests(now=now), 0)

    @override_settings(PUSH_VAPID_PUBLIC_KEY="", PUSH_VAPID_PRIVATE_KEY="")
    def test_unconfigured_server_does_not_send_or_advertise_push(self):
        self.client.get(reverse("notifications"))
        response = self.client.post(
            reverse("push_subscribe"), json.dumps(subscription_data()), content_type="application/json",
        )
        self.assertEqual(response.status_code, 503)
        self.assertFalse(PushSubscription.objects.exists())
