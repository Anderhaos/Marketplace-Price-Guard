from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant
from billing.access import effective_plan, has_plan_capability, has_smart_access
from .advisor import store_recommendations
from .models import GuardAccount, GuardEvent, Product, Upload


@override_settings(SAAS_PIN_AUTH_ENABLED=False, SAAS_ACCESS_ENFORCED=True)
class StoreAdvisorTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user("advisor@example.org", "advisor@example.org", "strong-password-123")
        self.other = User.objects.create_user("other@example.org", "other@example.org", "strong-password-123")
        self.account = GuardAccount.objects.create(
            user=self.seller, token_ciphertext="opaque", name="Мой магазин",
            last_check_at=timezone.now(), initial_sync_completed_at=timezone.now(),
            default_allowed_discount=15,
        )
        self.other_account = GuardAccount.objects.create(user=self.other, name="Чужой магазин")
        Product.objects.create(account=self.account, nm_id=120, title="Товар А", current_discount=34)
        Product.objects.create(account=self.account, nm_id=121, title="Товар Б", current_discount=7)
        Product.objects.create(account=self.other_account, nm_id=999, title="Чужой секрет", current_discount=98)
        GuardEvent.objects.create(account=self.account, kind="price_changed", message="WB 120 updated")
        GuardEvent.objects.create(account=self.other_account, kind="price_changed", message="WB 999 updated")

    def grant(self, *, user=None, plan="smart", days=1, revoked=False):
        return AccessGrant.objects.create(
            user=user or self.seller, source="admin",
            source_ref=f"test:{(user or self.seller).pk}:{plan}:{timezone.now().timestamp()}",
            plan=plan, starts_at=timezone.now()-timedelta(minutes=1),
            ends_at=timezone.now()+timedelta(days=days),
            revoked_at=timezone.now() if revoked else None,
        )

    def test_recommendations_use_only_own_wb_observations(self):
        result = store_recommendations(self.account)
        self.assertEqual(result["metrics"]["over_limit"], 1)
        self.assertEqual(result["metrics"]["price_changes_24h"], 1)
        text = " ".join(x["title"]+" "+x["detail"] for x in result["recommendations"])
        self.assertIn("WB 120", text)
        self.assertNotIn("999", text)
        self.assertNotIn("Чужой", text)
        self.assertIn("не подтверждение финансового убытка", text)

    def test_staleness_and_pending_operations_are_not_called_success(self):
        old = timezone.now()-timedelta(days=2)
        self.account.last_check_at = old
        self.account.save(update_fields=["last_check_at"])
        Product.objects.filter(account=self.account).update(checked_at=old)
        Upload.objects.create(account=self.account, upload_id=77, status="pending")
        result = store_recommendations(self.account)
        self.assertTrue(result["is_stale"])
        text = " ".join(x["title"] for x in result["recommendations"])
        self.assertIn("Перепроверьте устаревшие товары", text)
        self.assertEqual(result["metrics"]["stale_products"], 2)
        self.assertIn("Проверьте подтверждение операций", text)

    def test_failed_check_does_not_make_old_wb_data_current(self):
        Product.objects.filter(account=self.account).update(checked_at=timezone.now()-timedelta(days=3))
        self.account.last_error = "error"
        self.account.last_check_at = timezone.now()
        self.account.save(update_fields=["last_error", "last_check_at"])
        result = store_recommendations(self.account)
        self.assertTrue(result["is_stale"])
        self.assertGreater(timezone.now()-result["last_observed_at"], timedelta(days=2))

    def test_one_fresh_sku_does_not_mask_stale_rest_of_store(self):
        old = timezone.now() - timedelta(days=2)
        Product.objects.filter(account=self.account, nm_id=120).update(checked_at=old)
        Product.objects.filter(account=self.account, nm_id=121).update(checked_at=timezone.now())
        result = store_recommendations(self.account)
        self.assertEqual(result["metrics"]["stale_products"], 1)
        self.assertTrue(result["is_stale"])
        self.assertEqual(result["metrics"]["active_products"], 2)
        self.assertTrue(any("устаревшие" in item["title"] for item in result["recommendations"]))

    def test_only_active_smart_grant_permits_advisor(self):
        self.client.force_login(self.seller)
        self.assertFalse(has_smart_access(self.seller.pk))
        self.assertEqual(self.client.get(reverse("store_advisor")).status_code, 403)
        self.grant(plan="standard")
        self.assertEqual(self.client.get(reverse("store_advisor")).status_code, 403)
        self.grant(plan="smart", revoked=True)
        self.assertEqual(self.client.get(reverse("store_advisor")).status_code, 403)
        self.grant(plan="smart")
        self.assertTrue(has_smart_access(self.seller.pk))
        response = self.client.get(reverse("store_advisor"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "WB 120")
        self.assertNotContains(response, "Чужой секрет")

    def test_get_does_not_call_wb(self):
        self.grant()
        self.client.force_login(self.seller)
        from unittest.mock import patch
        with patch("guard.views.WBClient") as wb:
            response = self.client.get(reverse("store_advisor"))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.client.post(reverse("store_advisor")).status_code, 405)
            wb.assert_not_called()

    def test_capability_matrix_matches_beclients_pattern(self):
        self.assertFalse(has_plan_capability(self.seller.pk, "discount_guard"))
        self.assertFalse(has_plan_capability(self.seller.pk, "ai_advice"))
        self.grant(plan="standard")
        self.assertEqual(effective_plan(self.seller.pk), "standard")
        self.assertTrue(has_plan_capability(self.seller.pk, "web_assistant_basic"))
        self.assertTrue(has_plan_capability(self.seller.pk, "discount_guard"))
        self.assertFalse(has_plan_capability(self.seller.pk, "ai_questions"))
        self.assertFalse(has_plan_capability(self.seller.pk, "store_advice"))
        self.grant(plan="smart")
        self.assertEqual(effective_plan(self.seller.pk), "smart")
        self.assertTrue(has_plan_capability(self.seller.pk, "store_advice"))
        self.assertTrue(has_plan_capability(self.seller.pk, "web_assistant_basic"))
        self.assertFalse(has_plan_capability(self.seller.pk, "unknown_capability"))

    def test_smart_expires_then_standard_remains(self):
        standard = self.grant(plan="standard", days=10)
        smart = self.grant(plan="smart", days=1)
        self.assertEqual(effective_plan(self.seller.pk), "smart")
        smart.revoked_at = timezone.now()
        smart.save(update_fields=["revoked_at"])
        self.assertEqual(effective_plan(self.seller.pk), "standard")
        self.assertFalse(has_plan_capability(self.seller.pk, "ai_advice"))
        self.assertTrue(has_plan_capability(self.seller.pk, "discount_guard"))

    def test_expired_entitlement_is_denied(self):
        grant = self.grant()
        grant.ends_at = timezone.now() - timedelta(minutes=1)
        grant.save(update_fields=["ends_at"])
        self.client.force_login(self.seller)
        self.assertEqual(self.client.get(reverse("store_advisor")).status_code, 403)
