"""Owner-configurable tariff features must be genuine server-side entitlements."""
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.access import (
    FEATURE_KEYS, capabilities_for_user, guard_is_permitted,
    has_plan_capability, has_smart_access,
)
from billing.models import AccessGrant, OwnerAISettings, OwnerOperationsSettings, OwnerTariffOffer
from guard.models import GuardAccount, GuardEvent, Product
from guard.smart_scheduled import narrate_scheduled_smart
from push.models import NotificationPreference, PushMessage
from push.service import create_smart_briefs, queue_new_events


@override_settings(
    SAAS_ACCESS_ENFORCED=True, SAAS_PIN_AUTH_ENABLED=False,
    SAAS_WB_NETWORK_ENABLED=True, SAAS_WB_READ_ONLY=True,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class TariffFeatureBuilderTests(TestCase):
    morning = datetime(2026, 10, 30, 5, 5, tzinfo=dt_timezone.utc)

    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="owner-builder@example.test", email="owner-builder@example.test", password="test",
        )
        self.standard = User.objects.create_user("standard-builder@example.test", password="test")
        self.smart = User.objects.create_user("smart-builder@example.test", password="test")
        self.standard_offer = OwnerTariffOffer.objects.create(
            slug="standard", name="Standard", price_kopeks=50000, enabled=False,
        )
        self.smart_offer = OwnerTariffOffer.objects.create(
            slug="smart", name="Smart", price_kopeks=99000, enabled=False,
        )
        for seller, plan in ((self.standard, "standard"), (self.smart, "smart")):
            AccessGrant.objects.create(
                user=seller, source="admin", source_ref=f"builder-{plan}",
                plan=plan, starts_at=self.morning-timedelta(days=2),
                ends_at=self.morning+timedelta(days=5),
            )
        self.store = GuardAccount.objects.create(
            user=self.standard, marketplace="wb", name="Synthetic WB",
            token_ciphertext="synthetic-fixture-only", default_allowed_discount=10,
            initial_sync_completed_at=self.morning, last_check_at=self.morning,
        )
        Product.objects.create(
            account=self.store, nm_id=4321, current_discount=18, checked_at=self.morning,
        )

    def test_legacy_defaults_and_explicit_empty_revokes_everything(self):
        self.assertIsNone(self.standard_offer.features)
        self.assertTrue(has_plan_capability(self.standard.pk, "discount_guard", at=self.morning))
        self.assertTrue(has_plan_capability(self.standard.pk, "promotion_audit", at=self.morning))
        self.assertTrue(has_plan_capability(self.smart.pk, "store_advice", at=self.morning))
        self.standard_offer.features = []
        self.standard_offer.save(update_fields=["features"])
        self.assertEqual(capabilities_for_user(self.standard.pk, at=self.morning), frozenset())
        self.assertFalse(guard_is_permitted(self.store))
        self.assertFalse(has_plan_capability(self.standard.pk, "notifications", at=self.morning))

    def test_only_owner_can_save_matrix_and_prices_remain_separate(self):
        url = "/admin/control/tariffs/"
        self.client.force_login(self.standard)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.owner)
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        for feature in FEATURE_KEYS:
            self.assertContains(page, feature)
        response = self.client.post(url, {
            "_panel": "tariff_standard",
            "tariff_standard-name": "Standard",
            "tariff_standard-price_rub": "500.00",
            "tariff_standard-features": ["store_advice", "ai_advice", "activity_history"],
        })
        self.assertEqual(response.status_code, 302)
        self.standard_offer.refresh_from_db()
        self.smart_offer.refresh_from_db()
        self.assertEqual(set(self.standard_offer.features), {"store_advice", "ai_advice", "activity_history"})
        self.assertEqual(self.standard_offer.price_kopeks, 50000)
        self.assertFalse(self.standard_offer.enabled)
        self.assertIsNone(self.smart_offer.features)
        self.assertTrue(has_smart_access(self.standard.pk, at=self.morning))
        self.assertFalse(has_plan_capability(self.standard.pk, "discount_guard", at=self.morning))
        self.assertTrue(has_smart_access(self.smart.pk, at=self.morning))

    def test_invalid_code_cannot_be_enabled_and_empty_checkbox_payload_disables(self):
        self.client.force_login(self.owner)
        url = "/admin/control/tariffs/"
        bad = self.client.post(url, {
            "_panel": "tariff_standard",
            "tariff_standard-name": "Standard",
            "tariff_standard-price_rub": "500",
            "tariff_standard-features": ["invented-super-admin"],
        })
        self.assertEqual(bad.status_code, 200)
        self.standard_offer.refresh_from_db()
        self.assertIsNone(self.standard_offer.features)
        empty = self.client.post(url, {
            "_panel": "tariff_standard",
            "tariff_standard-name": "Standard",
            "tariff_standard-price_rub": "500",
        })
        self.assertEqual(empty.status_code, 302)
        self.standard_offer.refresh_from_db()
        self.assertEqual(self.standard_offer.features, [])

    def test_immediately_revoked_guard_promotion_history_finance_and_navigation(self):
        self.assertTrue(guard_is_permitted(self.store))
        self.client.force_login(self.standard)
        self.assertContains(self.client.get("/history/"), "История")
        self.standard_offer.features = ["notifications"]
        self.standard_offer.save(update_fields=["features"])
        self.assertFalse(guard_is_permitted(self.store))
        self.assertEqual(self.client.get("/history/").status_code, 403)
        self.assertEqual(self.client.get("/products/4321/history-data/").status_code, 403)
        self.assertEqual(self.client.get("/promotions/").status_code, 403)
        self.assertEqual(self.client.get("/finance/").status_code, 403)
        self.assertEqual(self.client.get("/advisor/").status_code, 403)
        page = self.client.get("/stores/")
        self.assertNotContains(page, "Акции и риски")
        self.assertNotContains(page, "История проверок")
        self.assertNotContains(page, "Финансы WB")
        self.assertContains(page, "Сводки")

    def test_basic_push_disabled_cannot_queue_or_deliver(self):
        self.standard_offer.features = []
        self.standard_offer.save(update_fields=["features"])
        event = GuardEvent.objects.create(
            account=self.store, kind="confirmed", message="synthetic",
        )
        GuardEvent.objects.filter(pk=event.pk).update(created_at=timezone.now())
        self.assertEqual(queue_new_events(), 0)
        self.assertFalse(PushMessage.objects.filter(user=self.standard, kind="event").exists())

    def test_standard_can_receive_smart_brief_without_deepseek_when_owner_configures_it(self):
        OwnerOperationsSettings.objects.get_or_create(pk=1)
        OwnerAISettings.objects.create(smart_briefs_per_day=2, ai_enabled=False)
        NotificationPreference.objects.create(
            user=self.standard, daily_enabled=True, timezone_name="Europe/Moscow",
        )
        event = GuardEvent.objects.create(
            account=self.store, kind="discount_changed", message="synthetic",
        )
        GuardEvent.objects.filter(pk=event.pk).update(
            created_at=self.morning-timedelta(minutes=20),
        )
        self.standard_offer.features = ["store_advice"]
        self.standard_offer.save(update_fields=["features"])
        with patch("guard.smart_scheduled.gateway_chat") as provider:
            self.assertEqual(create_smart_briefs(now=self.morning), 1)
            provider.assert_not_called()
        self.assertTrue(PushMessage.objects.filter(
            user=self.standard, kind="smart", smart_slot="2026-10-30:08",
        ).exists())
        self.assertIsNone(narrate_scheduled_smart(
            self.standard, self.store, {"recommendations": []}, now=self.morning,
        ))
        self.assertEqual(create_smart_briefs(now=self.morning), 0)
