"""Goal regression tests reproducing the October 2026 audit defects."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db.models import F
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant
from guard.models import GuardAccount, Product, Upload
from guard.monitor import run_due_checks


@override_settings(SAAS_WB_NETWORK_ENABLED=True, SAAS_ACCESS_ENFORCED=True, SAAS_PIN_AUTH_ENABLED=False)
class GoalWorkerTests(TestCase):
    def account(self, name, *, grant=True, minutes_ago=1, **flags):
        now = timezone.now()
        user = User.objects.create_user(username=name, password="test-only")
        account = GuardAccount.objects.create(
            user=user, token_ciphertext="encrypted-test",
            next_check_at=now - timedelta(minutes=minutes_ago), **flags,
        )
        if grant:
            AccessGrant.objects.create(
                user=user, source="admin", source_ref="goal:" + name,
                starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=1),
            )
        return account

    def test_expired_sellers_cannot_starve_active_seller(self):
        for index in range(20):
            self.account("expired-" + str(index), grant=False, minutes_ago=100, enabled=True)
        valid = self.account("active", enabled=True)
        with patch("guard.monitor.decrypt_token", return_value="fake"), patch(
            "guard.monitor.check_account", return_value=False
        ) as checker:
            self.assertEqual(run_due_checks(), 1)
        checker.assert_called_once()
        self.assertEqual(checker.call_args[0][0].pk, valid.pk)

    def test_new_command_during_network_check_is_not_cleared(self):
        account = self.account("race", enabled=True)
        def concurrently_added(_account):
            GuardAccount.objects.filter(pk=account.pk).update(
                apply_requested_ids=[54321], rules_version=F("rules_version") + 1,
            )
            return False
        with patch("guard.monitor.decrypt_token", return_value="fake"), patch(
            "guard.monitor.check_account", side_effect=concurrently_added
        ):
            self.assertEqual(run_due_checks(), 1)
        account.refresh_from_db()
        self.assertEqual(account.apply_requested_ids, [54321])
        self.assertLessEqual(account.next_check_at, timezone.now())

    def test_waiting_wb_upload_preserves_new_user_intent(self):
        account = self.account("waiting", check_requested=True, apply_requested_ids=[76543])
        Upload.objects.create(account=account, upload_id=100, status="pending")
        with patch("guard.monitor.decrypt_token", return_value="test.jwt.token"), patch(
            "guard.monitor.WBClient"
        ) as client_class:
            client_class.return_value.get_upload_state.return_value = 1
            self.assertEqual(run_due_checks(), 1)
            client_class.return_value.submit_discounts.assert_not_called()
        account.refresh_from_db()
        self.assertEqual(account.apply_requested_ids, [76543])
        self.assertTrue(account.check_requested)

    def test_manual_bulk_over_thousand_is_split_not_dropped(self):
        account = self.account("bulk", apply_all_requested=True)
        with patch("guard.monitor.decrypt_token", return_value="fake.jwt.token"), patch(
            "guard.monitor.WBClient"
        ) as client_class:
            client = client_class.return_value
            client.get_products.return_value = [
                {"nmID": number, "discount": 30, "vendorCode": str(number)}
                for number in range(1, 1002)
            ]
            client.get_product_cards.return_value = []
            client.submit_discounts.return_value = 6543
            self.assertEqual(run_due_checks(), 1)
            sent = client.submit_discounts.call_args.args[0]
        self.assertEqual(len(sent), 1000)
        account.refresh_from_db()
        self.assertTrue(account.apply_all_requested)
        self.assertTrue(account.check_requested)
        self.assertEqual(Upload.objects.get(account=account).status, "pending")

    def test_dashboard_get_never_calls_wb(self):
        account = self.account("reading", check_requested=True)
        self.client.force_login(account.user)
        with patch("guard.views._check_now", side_effect=AssertionError("GET must not fetch WB")):
            response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        account.refresh_from_db()
        self.assertTrue(account.check_requested)

    def test_zero_percent_requires_confirmation_before_mass_changes(self):
        account = self.account("discount-zero", enabled=False, default_allowed_discount=0)
        self.client.force_login(account.user)
        response = self.client.post(reverse("dashboard"), {
            "action": "toggle_autocontrol", "enabled": "on",
        })
        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        self.assertFalse(account.enabled)
        response = self.client.post(reverse("dashboard"), {
            "action": "toggle_autocontrol", "enabled": "on",
            "confirm_zero_discount": "1",
        })
        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        self.assertTrue(account.enabled)

    def test_zero_percent_apply_all_requires_confirmation(self):
        account = self.account("mass-zero", enabled=False)
        self.client.force_login(account.user)
        response = self.client.post(reverse("dashboard"), {
            "action": "apply_all", "default_allowed_discount": "0",
        })
        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        self.assertFalse(account.apply_all_requested)


@override_settings(SAAS_PIN_AUTH_ENABLED=True, SECURE_SSL_REDIRECT=False)
class PinGoalTests(TestCase):
    def test_repeat_lock_escalates_from_fifteen_minutes_to_one_hour(self):
        from guard.models import EmailPinLoginThrottle
        user = User.objects.create_user(
            username="pin-goal@example.test", email="pin-goal@example.test",
            password="1234",
        )
        now = timezone.now()
        EmailPinLoginThrottle.objects.create(
            email=user.email, attempts=4, lock_count=1,
            first_failed_at=now, locked_until=now - timedelta(seconds=1),
        )
        response = self.client.post(reverse("login"), {
            "email": user.email, "pin": "8765",
        })
        self.assertEqual(response.status_code, 200)
        throttle = EmailPinLoginThrottle.objects.get(email=user.email)
        self.assertEqual(throttle.lock_count, 2)
        self.assertGreater(throttle.locked_until, now + timedelta(minutes=59))
