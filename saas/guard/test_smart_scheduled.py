"""Acceptance for scheduled-only Smart summaries and owner ruble budget."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from billing.models import AccessGrant, OwnerAISettings
from billing.owner_readiness import integration_readiness
from guard.models import AssistantAIRequest, GuardAccount, Product
from guard.smart_scheduled import narrate_scheduled_smart
from push.models import NotificationPreference


@override_settings(SAAS_PIN_AUTH_ENABLED=False, SAAS_ACCESS_ENFORCED=True)
class ScheduledSmartTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("smart-owner@example.test", password="test")
        self.other = User.objects.create_user("other-tenant@example.test", password="test")
        self.store = GuardAccount.objects.create(
            user=self.user, marketplace="wb", name="Мой WB", token_ciphertext="fake-test-token",
            initial_sync_completed_at=timezone.now(), last_check_at=timezone.now(),
        )
        self.foreign = GuardAccount.objects.create(
            user=self.other, marketplace="wb", name="Чужой WB", token_ciphertext="foreign-secret",
        )
        Product.objects.create(account=self.store, nm_id=123, current_discount=18, checked_at=timezone.now())
        AccessGrant.objects.create(
            user=self.user, plan="smart", source="admin", source_ref="scheduled-smart-test",
            starts_at=timezone.now()-timedelta(hours=1),
            ends_at=timezone.now()+timedelta(days=2),
        )
        self.pref = NotificationPreference.objects.create(
            user=self.user, timezone_name="Europe/Moscow", daily_enabled=True,
            smart_ai_consent_at=timezone.now(),
        )
        self.ai = OwnerAISettings.objects.create(
            ai_enabled=True, ai_platform_cost_cap_confirmed=True,
            gateway_model_id="deepseek/deepseek-flash", monthly_budget_kopeks=100000,
            smart_reserve_kopeks=100, smart_briefs_per_day=2,
        )
        self.facts = {"metrics": {"over_limit": 1}, "actions": [
            {"title": "Проверьте скидку товара WB 123", "detail": "Скидка 18%, предел 10%."}
        ]}

    @patch("guard.smart_scheduled.runtime_gateway", return_value=("https://platform.svdev.pro/v1", "fake"))
    @patch("guard.smart_scheduled.gateway_chat", return_value=(
        "Проверьте товары со скидкой выше предела.",
        {"input_tokens": 80, "output_tokens": 18},
    ))
    def test_one_optimized_deepseek_request_and_per_user_usage(self, model, catalog):
        answer = narrate_scheduled_smart(self.user, self.store, self.facts)
        self.assertIn("Проверьте", answer)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(model.call_args.kwargs["model_id"], "deepseek/deepseek-flash")
        self.assertNotIn("tools", model.call_args.kwargs)
        import json
        payload = json.dumps(model.call_args.kwargs["messages"], ensure_ascii=False)
        self.assertNotIn("foreign-secret", payload)
        self.assertNotIn("fake-test-token", payload)
        row = AssistantAIRequest.objects.get()
        self.assertEqual((row.source, row.status, row.input_tokens, row.output_tokens),
                         ("scheduled", "success", 80, 18))
        stats = integration_readiness()["ai"]
        self.assertEqual(stats["monthly_budget_rub"], 1000)
        self.assertEqual(stats["reserved_rub"], 1)
        self.assertEqual(stats["seller_usage"][0]["user__email"], self.user.username)
        self.assertEqual(stats["seller_usage"][0]["calls"], 1)

    @patch("guard.smart_scheduled.gateway_chat")
    def test_opt_out_or_unverified_cap_makes_zero_calls(self, model):
        self.pref.smart_ai_consent_at = None
        self.pref.save(update_fields=["smart_ai_consent_at"])
        self.assertIsNone(narrate_scheduled_smart(self.user, self.store, self.facts))
        self.pref.smart_ai_consent_at = timezone.now()
        self.pref.save(update_fields=["smart_ai_consent_at"])
        self.ai.ai_platform_cost_cap_confirmed = False
        self.ai.save(update_fields=["ai_platform_cost_cap_confirmed"])
        self.assertIsNone(narrate_scheduled_smart(self.user, self.store, self.facts))
        model.assert_not_called()
        self.assertEqual(AssistantAIRequest.objects.count(), 0)

    @patch("guard.smart_scheduled.runtime_gateway", return_value=("https://platform.svdev.pro/v1", "fake"))
    @patch("guard.smart_scheduled.gateway_chat", return_value=(
        "Компания заработала 999999 рублей.",
        {"input_tokens": 80, "output_tokens": 25},
    ))
    def test_unverified_financial_claim_is_rejected_and_audited(self, model, catalog):
        self.assertIsNone(narrate_scheduled_smart(self.user, self.store, self.facts))
        self.assertEqual(AssistantAIRequest.objects.get().status, "failed")

    @patch("guard.smart_scheduled.gateway_chat")
    def test_other_store_never_sent_to_model(self, model):
        self.assertIsNone(narrate_scheduled_smart(self.user, self.foreign, self.facts))
        self.assertFalse(AssistantAIRequest.objects.exists())
        model.assert_not_called()

    @patch("guard.smart_scheduled.gateway_chat")
    def test_monthly_budget_cap_prevents_outbound_calls(self, model):
        self.ai.monthly_budget_kopeks = 50
        self.ai.save(update_fields=["monthly_budget_kopeks"])
        self.assertIsNone(narrate_scheduled_smart(self.user, self.store, self.facts))
        self.assertFalse(AssistantAIRequest.objects.exists())
        model.assert_not_called()
