"""R2 Smart tool orchestration: explicit opt-in, budgets, tenant context and wire checks."""
import json
from datetime import timedelta
from unittest.mock import patch, AsyncMock

from django.contrib.auth.models import User
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant, OwnerAISettings
from guard.ai_gateway import AIGatewayUnavailable, gateway_choose_tool
from guard.models import AssistantAIRequest, AssistantMessage, GuardAccount, Product
from nosale_mcp.client import MCPToolUnavailable, call_owned_tool, definitions, validated_request


class ToolContractTests(SimpleTestCase):
    def test_only_bounded_readonly_arguments_without_store_or_token(self):
        self.assertEqual(validated_request("wb_products", {"limit": 10, "offset": 0}),
                         ("wb_products", {"limit": 10, "offset": 0}))
        for name, args in (
            ("wb_products", {"store_id": 1}), ("wb_products", {"user_id": 1}),
            ("wb_products", {"token": "anything"}), ("wb_products", {"limit": 10000}),
            ("wb_products", {"offset": -5}), ("wb_products", {"limit": 5.0}),
            ("wb_products", {"limit": True}), ("wb_promotions", {"id": 99}),
            ("wb_update_price", {}), ("ozon_adapter_status", {"store_id": 1}),
        ):
            with self.subTest(name=name, args=args), self.assertRaises(MCPToolUnavailable):
                validated_request(name, args)
        tools = definitions("wb")
        self.assertLessEqual(len(tools), 5)
        for tool in tools:
            props = tool["function"]["parameters"]["properties"]
            self.assertNotIn("store_id", props)
            self.assertNotIn("user_id", props)
            self.assertNotIn("token", props)
        self.assertEqual([x["function"]["name"] for x in definitions("ozon")],
                         ["ozon_adapter_status"])

    @override_settings(
        NOSALE_AI_GATEWAY_URL="https://platform.svdev.pro/v1/chat/completions",
        NOSALE_AI_SERVICE_TOKEN="wire-mocked-token-only",
    )
    def test_ai_platform_selects_single_allowlisted_tool_with_no_mcp_bearer(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _max):
                return json.dumps({
                    "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{
                        "id": "arbitrary_untrusted",
                        "type": "function",
                        "function": {"name": "wb_products", "arguments": '{"limit":2}'},
                    }]}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                }).encode()
        class Opener:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return Response()
        op = Opener()
        with patch("guard.ai_gateway.build_opener", return_value=op):
            selected, content, usage = gateway_choose_tool(
                model_id="allowed-chat", messages=[{"role": "user", "content": "Какие товары?"}],
                tools=definitions("wb"), timeout_seconds=7,
            )
        self.assertEqual(selected, {"name": "wb_products", "arguments": {"limit": 2}})
        self.assertIsNone(content)
        self.assertEqual(usage, {"input_tokens": 100, "output_tokens": 20})
        payload = json.loads(op.request.data)
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertFalse(payload["parallel_tool_calls"])
        self.assertEqual(op.request.full_url, "https://platform.svdev.pro/v1/chat/completions")
        self.assertNotIn("mcp:8099", json.dumps(payload))
        self.assertNotIn("Bearer", json.dumps(payload))
        self.assertEqual(op.request.get_header("Authorization"), "Bearer wire-mocked-token-only")

    @override_settings(
        NOSALE_AI_GATEWAY_URL="https://platform.svdev.pro/v1/chat/completions",
        NOSALE_AI_SERVICE_TOKEN="wire-mocked-token-only",
    )
    def test_gateway_rejects_unauthorized_and_parallel_tool_calls(self):
        class Response:
            status = 200
            def __init__(self, payload): self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _max): return json.dumps(self.payload).encode()
        for calls in (
            [{"function": {"name": "wb_update_price", "arguments": "{}"}}],
            [{"function": {"name": "wb_products", "arguments": "{}"}} for _ in range(2)],
            [{"function": {"name": "wb_products", "arguments": "not-json"}}],
            [{"function": {"name": "wb_products", "arguments": "[]"}}],
        ):
            with self.subTest(calls=calls), patch("guard.ai_gateway.build_opener") as opener:
                opener.return_value.open.return_value = Response({
                    "choices": [{"message": {"content": None, "tool_calls": calls}}]
                })
                with self.assertRaises(AIGatewayUnavailable):
                    gateway_choose_tool(
                        model_id="m", messages=[{"role": "user", "content": "test"}],
                        tools=definitions("wb"), timeout_seconds=5,
                    )


@override_settings(
    SAAS_PIN_AUTH_ENABLED=False, SAAS_ACCESS_ENFORCED=True, SAAS_WB_NETWORK_ENABLED=False,
    NOSALE_AI_GATEWAY_URL="https://platform.svdev.pro/v1/chat/completions",
    NOSALE_AI_SERVICE_TOKEN="test-only-nosale-project",
    NOSALE_AI_RESERVE_KOPEKS_PER_CALL=100,
)
class SmartMCPIntegrationTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            "tool-seller@example.test", "tool-seller@example.test", "safe-password",
        )
        self.other = User.objects.create_user(
            "tool-other@example.test", "tool-other@example.test", "safe-password",
        )
        self.store = GuardAccount.objects.create(
            user=self.seller, marketplace="wb", name="Магазин продавца",
            token_ciphertext="synthetic-not-a-real-token",
            default_allowed_discount=10,
            initial_sync_completed_at=timezone.now(),
            last_check_at=timezone.now(),
        )
        self.foreign = GuardAccount.objects.create(
            user=self.other, name="Чужой секретный магазин",
        )
        Product.objects.create(
            account=self.store, nm_id=100, current_discount=18, checked_at=timezone.now(),
        )
        Product.objects.create(
            account=self.foreign, nm_id=987654321, current_discount=99,
        )
        self.grant = AccessGrant.objects.create(
            user=self.seller, source="admin", source_ref="mcp-smart-test",
            plan="smart", starts_at=timezone.now()-timedelta(hours=1),
            ends_at=timezone.now()+timedelta(days=1),
        )
        self.ai = OwnerAISettings.objects.create(
            ai_enabled=True, gateway_model_id="published-model",
            smart_questions_per_day=4, monthly_budget_kopeks=600,
        )
        self.client.force_login(self.seller)

    def ask(self, **override):
        fields = {
            "question": "Что со скидками?", "mode": "smart",
            "ai_consent": "yes", "mcp_consent": "yes",
        }
        fields.update(override)
        return self.client.post(reverse("assistant_chat"), fields)

    def test_smart_chooses_owned_mcp_tool_then_grounded_answer(self):
        tool_info = {"marketplace": "wb", "items": [
            {"nm_id": 100, "current_discount_pct": 18, "allowed_discount_pct": 10},
        ], "source": "NoSale saved observations"}
        with patch("guard.smart_assistant.gateway_choose_tool", return_value=(
            {"name": "wb_products", "arguments": {"limit": 1}}, None,
            {"input_tokens": 45, "output_tokens": 12},
        )) as selection, patch("guard.smart_assistant.call_owned_tool", return_value=tool_info) as tool, \
             patch("guard.smart_assistant.gateway_chat", return_value=(
                 "Проверьте товар 100: скидка 18%, допустимо 10%.",
                 {"input_tokens": 100, "output_tokens": 22},
             )) as narration, patch("guard.views.WBClient") as wb:
            response = self.ask()
            wb.assert_not_called()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(selection.call_count, 1)
        self.assertEqual(tool.call_count, 1)
        self.assertEqual(tool.call_args.args[0], self.seller)
        self.assertEqual(tool.call_args.args[1], self.store)
        self.assertEqual(tool.call_args.args[2:], ("wb_products", {"limit": 1}))
        payload = json.dumps(narration.call_args.kwargs["messages"], ensure_ascii=False)
        self.assertIn("call_nosale_1", payload)
        self.assertIn("current_discount_pct", payload)
        self.assertNotIn("987654321", payload)
        self.assertNotIn("synthetic-not-a-real-token", payload)
        self.assertNotIn("Чужой секретный магазин", payload)
        row = AssistantAIRequest.objects.get()
        self.assertEqual((row.reserved_kopeks, row.input_tokens, row.output_tokens, row.status),
                         (200, 145, 34, "success"))
        self.assertEqual(AssistantMessage.objects.get().mode, "smart")

    def test_without_separate_mcp_consent_uses_previous_ai_path(self):
        with patch("guard.smart_assistant.gateway_choose_tool") as chooser, \
             patch("guard.smart_assistant.call_owned_tool") as tool, \
             patch("guard.smart_assistant.gateway_chat", return_value=(
                 "Проверьте лимит скидки 10%.", {"input_tokens": 8, "output_tokens": 6},
             )):
            self.assertEqual(self.ask(mcp_consent="").status_code, 302)
            chooser.assert_not_called()
            tool.assert_not_called()
        self.assertEqual(AssistantAIRequest.objects.get().reserved_kopeks, 100)

    def test_no_ai_consent_denies_mcp_and_provider_without_spend(self):
        with patch("guard.smart_assistant.gateway_choose_tool") as chooser, \
             patch("guard.smart_assistant.call_owned_tool") as tool:
            self.assertEqual(self.ask(ai_consent="").status_code, 400)
            chooser.assert_not_called()
            tool.assert_not_called()
        self.assertFalse(AssistantAIRequest.objects.exists())
        self.assertFalse(AssistantMessage.objects.exists())

    def test_mcp_tool_unavailable_falls_back_without_sending_tool_data(self):
        with patch("guard.smart_assistant.gateway_choose_tool", return_value=(
            {"name": "wb_products", "arguments": {}}, None,
            {"input_tokens": 1, "output_tokens": 1},
        )), patch("guard.smart_assistant.call_owned_tool", side_effect=MCPToolUnavailable("offline")), \
            patch("guard.smart_assistant.gateway_chat") as ai:
            self.assertEqual(self.ask().status_code, 302)
            ai.assert_not_called()
        self.assertEqual(AssistantAIRequest.objects.get().status, "failed")
        self.assertEqual(AssistantMessage.objects.get().mode, "basic")

    def test_revocation_during_tool_denies_reply_persistence(self):
        def revoke(*_):
            AccessGrant.objects.filter(pk=self.grant.pk).update(revoked_at=timezone.now())
            return {"items": [{"nm_id": 100}]}
        with patch("guard.smart_assistant.gateway_choose_tool", return_value=(
            {"name": "wb_products", "arguments": {}}, None,
            {"input_tokens": 1, "output_tokens": 1},
        )), patch("guard.smart_assistant.call_owned_tool", side_effect=revoke), \
             patch("guard.smart_assistant.gateway_chat") as narration:
            self.assertEqual(self.ask().status_code, 403)
            narration.assert_not_called()
        self.assertEqual(AssistantAIRequest.objects.get().status, "failed")
        self.assertFalse(AssistantMessage.objects.exists())

    def test_hallucinated_finance_and_unknown_tool_fail_closed(self):
        with patch("guard.smart_assistant.gateway_choose_tool", return_value=(
            {"name": "wb_promotions", "arguments": {}}, None,
            {"input_tokens": 1, "output_tokens": 1},
        )), patch("guard.smart_assistant.call_owned_tool", return_value={
            "items": [{"id": 12, "funding": "unknown"}],
        }), patch("guard.smart_assistant.gateway_chat", return_value=(
            "WB профинансирует скидку за счёт площадки и прибыль составит 120 ₽.",
            {"input_tokens": 10, "output_tokens": 10},
        )):
            self.assertEqual(self.ask().status_code, 302)
        self.assertEqual(AssistantAIRequest.objects.get().status, "failed")
        self.assertEqual(AssistantMessage.objects.get().mode, "basic")

    def test_billing_cap_prevents_two_call_session_before_model(self):
        self.ai.monthly_budget_kopeks = 100  # below 2x per-call reserve
        self.ai.save(update_fields=["monthly_budget_kopeks"])
        with patch("guard.smart_assistant.gateway_choose_tool") as chooser, \
             patch("guard.smart_assistant.call_owned_tool") as tool:
            self.assertEqual(self.ask().status_code, 302)
            chooser.assert_not_called()
            tool.assert_not_called()
        self.assertFalse(AssistantAIRequest.objects.exists())
