"""R4: Smart advice is tenant-scoped, budgeted and sent only to AI Platform."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant, OwnerAISettings
from .ai_gateway import AIGatewayUnavailable, gateway_chat
from .models import AssistantAIRequest, AssistantMessage, GuardAccount, Product
from .smart_assistant import _numbers_are_grounded, smart_store_reply
from .stores import SESSION_KEY


@override_settings(
    SAAS_PIN_AUTH_ENABLED=False, SAAS_ACCESS_ENFORCED=True,
    NOSALE_AI_GATEWAY_URL="https://platform.svdev.pro/v1/chat/completions",
    NOSALE_AI_SERVICE_TOKEN="test-only-nosale-service-token",
    NOSALE_AI_RESERVE_KOPEKS_PER_CALL=100,
)
class SmartAssistantTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user("smart@example.test", "smart@example.test", "pass")
        self.other = User.objects.create_user("other-smart@example.test", "other-smart@example.test", "pass")
        self.store = GuardAccount.objects.create(
            user=self.seller, name="Магазин собственника", token_ciphertext="opaque",
            default_allowed_discount=10,
            initial_sync_completed_at=timezone.now(), last_check_at=timezone.now(),
        )
        self.foreign = GuardAccount.objects.create(
            user=self.other, name="Чужая коммерческая тайна", token_ciphertext="another",
        )
        Product.objects.create(account=self.store, nm_id=100, current_discount=18, checked_at=timezone.now())
        Product.objects.create(account=self.foreign, nm_id=987654, current_discount=99)
        self.client.force_login(self.seller)
        self.ai = OwnerAISettings.objects.create(
            ai_enabled=True, gateway_model_id="published-deepseek-id",
            smart_questions_per_day=2, monthly_budget_kopeks=500,
        )
        AccessGrant.objects.create(
            user=self.seller, source="admin", source_ref="test-smart-now", plan="smart",
            starts_at=timezone.now()-timedelta(hours=1), ends_at=timezone.now()+timedelta(days=1),
        )

    def ask(self, question="Что проверить в магазине?", **overrides):
        values = {"question": question, "mode": "smart", "ai_consent": "yes"}
        values.update(overrides)
        return self.client.post(reverse("assistant_chat"), values)

    def test_successful_smart_answer_through_gateway_only(self):
        with patch("guard.smart_assistant.gateway_chat", return_value=(
            "Проверьте товары выше предела скидки и актуальность наблюдений.",
            {"input_tokens": 300, "output_tokens": 35},
        )) as gateway, patch("guard.views.WBClient") as wb:
            response = self.ask()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(gateway.call_count, 1)
        self.assertEqual(gateway.call_args.kwargs["model_id"], "published-deepseek-id")
        payload = json.dumps(gateway.call_args.kwargs["messages"], ensure_ascii=False)
        self.assertIn("over_limit", payload)
        self.assertNotIn("987654", payload)
        self.assertNotIn("Чужая коммерческая тайна", payload)
        self.assertNotIn("opaque", payload)
        wb.assert_not_called()
        entry = AssistantAIRequest.objects.get()
        self.assertEqual((entry.status, entry.input_tokens, entry.output_tokens),
                         ("success", 300, 35))
        msg = AssistantMessage.objects.get()
        self.assertEqual(msg.mode, "smart")
        self.assertContains(self.client.get(reverse("assistant_chat")), msg.answer)

    def test_smart_daily_limit_is_atomic_reservation(self):
        with patch("guard.smart_assistant.gateway_chat", return_value=(
            "Перепроверьте установленный предел скидки.", {"input_tokens": 80, "output_tokens": 20},
        )) as gateway:
            self.assertEqual(self.ask().status_code, 302)
            self.assertEqual(self.ask("Что изменилось?").status_code, 302)
            self.assertEqual(self.ask("Ещё один вопрос").status_code, 302)
        self.assertEqual(gateway.call_count, 2)
        self.assertEqual(AssistantAIRequest.objects.filter(user=self.seller).count(), 2)
        self.assertEqual(AssistantMessage.objects.filter(user=self.seller, mode="basic").count(), 1)

    def test_owner_monthly_reservation_budget_limits_requests(self):
        self.ai.monthly_budget_kopeks = 100
        self.ai.smart_questions_per_day = 10
        self.ai.save()
        with patch("guard.smart_assistant.gateway_chat", return_value=(
            "Проверьте товар выше предела.", {"input_tokens": 10, "output_tokens": 10},
        )) as gateway:
            self.assertEqual(self.ask().status_code, 302)
            self.assertEqual(self.ask().status_code, 302)
        self.assertEqual(gateway.call_count, 1)
        self.assertEqual(AssistantAIRequest.objects.count(), 1)

    def test_ai_disabled_is_safe_basic_fallback(self):
        self.ai.ai_enabled = False
        self.ai.save(update_fields=["ai_enabled"])
        with patch("guard.smart_assistant.gateway_chat") as gateway:
            self.assertEqual(self.ask().status_code, 302)
            gateway.assert_not_called()
        self.assertEqual(AssistantAIRequest.objects.count(), 0)
        self.assertEqual(AssistantMessage.objects.get().mode, "basic")

    def test_disabled_smart_fallback_obeys_shared_chat_limit(self):
        self.ai.ai_enabled = False
        self.ai.save(update_fields=["ai_enabled"])
        with patch("guard.smart_assistant.gateway_chat") as gateway:
            for index in range(8):
                self.assertEqual(self.ask(f"Статус магазина {index}").status_code, 302)
            self.assertEqual(self.ask("Девятый вопрос").status_code, 429)
            gateway.assert_not_called()
        self.assertEqual(AssistantMessage.objects.filter(user=self.seller).count(), 8)
        self.assertEqual(AssistantAIRequest.objects.filter(user=self.seller).count(), 0)

    def test_switching_basic_and_smart_cannot_bypass_message_limit(self):
        basic_url = reverse("assistant_chat")
        for index in range(8):
            response = self.client.post(basic_url, {
                "question": f"Покажи товары {index}", "mode": "basic",
            })
            self.assertEqual(response.status_code, 302)
        with patch("guard.smart_assistant.gateway_chat") as gateway:
            self.assertEqual(self.ask("Обойди лимит в Smart").status_code, 429)
            gateway.assert_not_called()
        self.assertEqual(AssistantAIRequest.objects.count(), 0)
        self.assertEqual(AssistantMessage.objects.count(), 8)

    def test_no_consent_no_outbound_ai(self):
        with patch("guard.smart_assistant.gateway_chat") as gateway:
            self.assertEqual(self.ask(ai_consent="").status_code, 400)
            gateway.assert_not_called()
        self.assertEqual(AssistantAIRequest.objects.count(), 0)

    def test_standard_cannot_force_smart(self):
        AccessGrant.objects.filter(user=self.seller).update(revoked_at=timezone.now())
        AccessGrant.objects.create(
            user=self.seller, source="admin", source_ref="test-standard", plan="standard",
            starts_at=timezone.now()-timedelta(hours=1), ends_at=timezone.now()+timedelta(days=1),
        )
        with patch("guard.smart_assistant.gateway_chat") as gateway:
            self.assertEqual(self.ask().status_code, 403)
            gateway.assert_not_called()
        self.assertEqual(AssistantAIRequest.objects.count(), 0)

    def test_seller_cannot_select_foreign_account_for_gateway(self):
        session = self.client.session
        session[SESSION_KEY] = self.foreign.pk
        session.save()
        with patch("guard.smart_assistant.gateway_chat", return_value=(
            "Проверьте лимит скидки.", {"input_tokens": 20, "output_tokens": 10},
        )) as gateway:
            self.assertEqual(self.ask().status_code, 302)
        self.assertNotIn("Чужая коммерческая тайна", str(gateway.call_args))
        self.assertEqual(AssistantAIRequest.objects.get().account_id, self.store.pk)
        with self.assertRaises(PermissionError):
            smart_store_reply(self.seller.pk, self.foreign, "Статус")

    def test_hallucinated_numbers_fail_closed_and_report_basic(self):
        self.assertFalse(_numbers_are_grounded("Прибыль 987654 рублей", {"metrics": {"over_limit": 1}}))
        with patch("guard.smart_assistant.gateway_chat", return_value=(
            "Магазин заработал 987654 рублей.", {"input_tokens": 1, "output_tokens": 1},
        )):
            self.assertEqual(self.ask().status_code, 302)
        self.assertEqual(AssistantAIRequest.objects.get().status, "failed")
        self.assertEqual(AssistantMessage.objects.get().mode, "basic")
        self.assertNotIn("987654", AssistantMessage.objects.get().answer)

    def test_financial_hallucination_rejected_even_with_matching_discount_number(self):
        # Current numeric-only check could accept "18 рублей прибыли" because
        # 18 was observed as a discount. There is no financial source for it.
        evidence = {"metrics": {"over_limit": 1}, "recommendations": [
            {"detail": "Товар: скидка 18%, предел 10%", "observed_at": "2026-10-23T10:00:00"}
        ]}
        self.assertFalse(_numbers_are_grounded("Прибыль составила 18 рублей.", evidence))
        self.assertFalse(_numbers_are_grounded("WB финансирует скидку 18%.", evidence))
        self.assertFalse(_numbers_are_grounded("Выручка выросла на 10%.", evidence))
        self.assertTrue(_numbers_are_grounded("Проверьте товар со скидкой 18%.", evidence))
        self.assertFalse(_numbers_are_grounded("Проверьте скидку 987654%.", evidence))

    def test_finance_claim_in_model_response_falls_back_to_verified_basic(self):
        with patch("guard.smart_assistant.gateway_chat", return_value=(
            "Прибыль по товару равна 18 рублей.", {"input_tokens": 10, "output_tokens": 11},
        )):
            response = self.ask("Что проверить?")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AssistantAIRequest.objects.get().status, "failed")
        self.assertEqual(AssistantMessage.objects.get().mode, "basic")
        self.assertNotIn("18 рублей", AssistantMessage.objects.get().answer)

    def test_provider_and_bearer_secrets_never_enter_chat_history(self):
        for question in (
            "Мой ключ sk-" + "x" * 28 + ", что с ним?",
            "Authorization: Bearer " + "abc123" * 5,
            "API key: " + "super-secret-value-12345",
        ):
            with self.subTest(question=question[:16]), patch("guard.smart_assistant.gateway_chat") as gateway:
                self.assertEqual(self.ask(question).status_code, 400)
                gateway.assert_not_called()
        self.assertEqual(AssistantAIRequest.objects.count(), 0)
        self.assertEqual(AssistantMessage.objects.count(), 0)

    def test_sensitive_api_token_not_persisted_or_sent(self):
        token = "eyJ" + "a" * 30 + "." + "b" * 30
        with patch("guard.smart_assistant.gateway_chat") as gateway:
            self.assertEqual(self.ask("Посмотри токен " + token).status_code, 400)
            gateway.assert_not_called()
        self.assertEqual(AssistantAIRequest.objects.count(), 0)
        self.assertEqual(AssistantMessage.objects.count(), 0)


@override_settings(
    NOSALE_AI_GATEWAY_URL="https://evil.example/v1/chat/completions",
    NOSALE_AI_SERVICE_TOKEN="test-only",
)
class GatewayTransportTests(SimpleTestCase):
    def test_refuses_external_host_without_sending_request(self):
        with patch("guard.ai_gateway.build_opener") as opener:
            with self.assertRaises(AIGatewayUnavailable):
                gateway_chat(model_id="m", messages=[{"role": "user", "content": "hi"}],
                             max_output_tokens=50, timeout_seconds=5)
            opener.assert_not_called()


@override_settings(
    NOSALE_AI_GATEWAY_URL="https://platform.svdev.pro/v1/chat/completions",
    NOSALE_AI_SERVICE_TOKEN="test-service-token",
)
class GatewayWireContractTests(SimpleTestCase):
    def test_uses_scoped_bearer_and_openai_chat_format(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _max): return json.dumps({
                "choices": [{"message": {"role": "assistant", "content": "Проверьте статус"}}],
                "usage": {"prompt_tokens": 101, "completion_tokens": 12},
            }).encode()
        class Opener:
            def open(self, request, timeout):
                self.request, self.timeout = request, timeout
                return Response()
        opener = Opener()
        with patch("guard.ai_gateway.build_opener", return_value=opener):
            answer, usage = gateway_chat(
                model_id="published-model", messages=[{"role": "user", "content": "status"}],
                max_output_tokens=600, timeout_seconds=20,
            )
        self.assertEqual(answer, "Проверьте статус")
        self.assertEqual(usage, {"input_tokens": 101, "output_tokens": 12})
        self.assertEqual(opener.request.full_url, "https://platform.svdev.pro/v1/chat/completions")
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer test-service-token")
        self.assertEqual(opener.request.get_header("X-ai-project-key"), "nosale")
        self.assertEqual(opener.timeout, 20)
        posted = json.loads(opener.request.data)
        self.assertEqual(posted["model"], "published-model")
        self.assertFalse(posted["stream"])
        self.assertEqual(posted["max_tokens"], 600)
        self.assertNotIn("api_key", posted)

    def test_rejects_oversized_or_malformed_gateway_output(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _max): return b'{"choices":[]}'
        class Opener:
            def open(self, request, timeout): return Response()
        with patch("guard.ai_gateway.build_opener", return_value=Opener()):
            with self.assertRaises(AIGatewayUnavailable):
                gateway_chat(model_id="m", messages=[{"role": "user", "content": "hi"}],
                             max_output_tokens=50, timeout_seconds=5)
