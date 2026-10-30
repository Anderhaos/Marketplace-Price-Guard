"""R2 Smart tool orchestration: explicit opt-in, budgets, tenant context and wire checks."""
import json
from datetime import timedelta
from unittest.mock import patch, AsyncMock, Mock

from django.contrib.auth.models import User
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant, OwnerAISettings, OwnerMCPSettings
from guard.ai_gateway import AIGatewayUnavailable, gateway_choose_tool, gateway_chat
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
                    "choices": [{"message": {"role": "assistant", "content": None,
                       "reasoning_content": "reasoning_context_for_provider", "tool_calls": [{
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
        self.assertEqual(selected, {"name": "wb_products", "arguments": {"limit": 2}, "call_id": "arbitrary_untrusted",
                                    "reasoning_content": "reasoning_context_for_provider"})
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
    def test_second_round_preserves_model_call_id_and_disables_further_tools(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _max):
                return json.dumps({
                    "choices": [{"message": {"content": "Данные сохранены."}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 4},
                }).encode()
        class Opener:
            def open(self, request, timeout):
                self.request = request
                return Response()
        opener = Opener()
        followup = [
            {"role": "user", "content": "Проверь данные"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_from_original_model_123", "type": "function",
                "function": {"name": "wb_overview", "arguments": "{}"},
            }]},
            {"role": "tool", "tool_call_id": "call_from_original_model_123",
             "content": '{"read_only":true}'},
        ]
        with patch("guard.ai_gateway.build_opener", return_value=opener):
            reply, usage = gateway_chat(
                model_id="allowed-chat", messages=followup,
                max_output_tokens=120, timeout_seconds=7,
                tools=[x for x in definitions("wb") if x["function"]["name"] == "wb_overview"],
            )
        self.assertEqual(reply, "Данные сохранены.")
        self.assertEqual(usage, {"input_tokens": 20, "output_tokens": 4})
        posted = json.loads(opener.request.data)
        self.assertEqual(posted["tool_choice"], "none")
        self.assertFalse(posted["parallel_tool_calls"])
        self.assertEqual(posted["messages"][1]["tool_calls"][0]["id"], "call_from_original_model_123")
        self.assertEqual(posted["messages"][2]["tool_call_id"], "call_from_original_model_123")
        self.assertEqual(len(posted["tools"]), 1)

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
            {"name": "wb_products", "arguments": {"limit": 1}, "call_id": "call_real_model_id"}, None,
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
        self.assertIn("call_real_model_id", payload)
        self.assertNotIn("call_nosale_1", payload)
        self.assertEqual(narration.call_args.kwargs["tools"], definitions("wb"))
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
            {"name": "wb_promotions", "arguments": {}, "call_id": "call_finance_model"}, None,
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

    def test_disabled_mcp_never_reserves_budget_or_contacts_ai(self):
        policy = OwnerMCPSettings.runtime()
        self.assertIsNotNone(policy)
        policy.enabled = False
        policy.save(update_fields=["enabled"])
        with patch("guard.ai_gateway.build_opener") as net, \
             patch("nosale_mcp.client._call_tool", new_callable=AsyncMock) as tool:
            self.assertEqual(self.ask().status_code, 302)
            net.assert_not_called()
            tool.assert_not_called()
        self.assertFalse(AssistantAIRequest.objects.exists())
        self.assertEqual(AssistantMessage.objects.get().mode, "basic")

    def test_mcp_disabled_does_not_block_explicit_non_mcp_smart_question(self):
        policy = OwnerMCPSettings.runtime()
        policy.enabled = False
        policy.save(update_fields=["enabled"])
        with patch("guard.smart_assistant.gateway_chat", return_value=(
            "Проверьте скидку 18% при пределе 10%.",
            {"input_tokens": 20, "output_tokens": 10},
        )) as chat, patch("guard.smart_assistant.gateway_choose_tool") as select:
            self.assertEqual(self.ask(mcp_consent="").status_code, 302)
            chat.assert_called_once()
            select.assert_not_called()
        self.assertEqual(AssistantAIRequest.objects.get().reserved_kopeks, 100)
        self.assertEqual(AssistantMessage.objects.get().mode, "smart")

    def test_disabled_marketplace_adapter_does_not_reserve_ai(self):
        from billing.models import OwnerMCPAdapter
        self.assertEqual(
            OwnerMCPAdapter.objects.filter(slug="wb", enabled=True).update(enabled=False), 1,
        )
        with patch("guard.ai_gateway.build_opener") as net:
            self.assertEqual(self.ask().status_code, 302)
            net.assert_not_called()
        self.assertFalse(AssistantAIRequest.objects.exists())
        self.assertEqual(AssistantMessage.objects.get().mode, "basic")

    def test_mcp_connection_cleanup_does_not_close_an_atomic_transaction(self):
        # A direct MCP transport call is allowed inside another service's
        # transaction. PostgreSQL must not lose its connection at the end.
        for ambient_transaction in (True, False):
            with self.subTest(ambient_transaction=ambient_transaction):
                db_connection = Mock(in_atomic_block=ambient_transaction)
                with patch("nosale_mcp.client._call_tool", new_callable=AsyncMock,
                           return_value={"read_only": True}), \
                     patch("nosale_mcp.client.connections.all",
                           return_value=[db_connection]):
                    answer = call_owned_tool(self.seller, self.store, "wb_overview", {})
                self.assertEqual(answer, {"read_only": True})
                if ambient_transaction:
                    db_connection.close_if_unusable_or_obsolete.assert_not_called()
                else:
                    db_connection.close_if_unusable_or_obsolete.assert_called_once_with()

    def test_two_round_ai_platform_and_signed_owned_mcp_flow_no_network(self):
        """Real view, real AI HTTP serialization, signed MCP identity, no live I/O.

        Only the AI Platform opener and internal MCP network transport are
        replaced with deterministic fixtures; no WB or LLM connections occur.
        """
        from nosale_mcp.auth import verify_store_token

        class FakeResponse:
            status = 200
            def __init__(self, payload):
                self.payload = payload
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def read(self, _max):
                return json.dumps(self.payload, ensure_ascii=False).encode()

        class FakeAIPlatform:
            def __init__(self):
                self.requests = []
            def open(self, request, timeout):
                self.requests.append(request)
                payload = json.loads(request.data)
                self_owner = "Чужой секретный магазин"
                self.assert_no_secrets(payload, self_owner)
                if len(self.requests) == 1:
                    assert payload["tool_choice"] == "auto"
                    assert payload["parallel_tool_calls"] is False
                    assert "wb_products" in [item["function"]["name"] for item in payload["tools"]]
                    return FakeResponse({
                        "choices": [{"message": {"content": None, "tool_calls": [{
                            "id": "call_exact_model_123", "type": "function",
                            "function": {"name": "wb_products", "arguments": '{"limit":1}'}
                        }]}}],
                        "usage": {"prompt_tokens": 31, "completion_tokens": 8},
                    })
                assert len(self.requests) == 2
                assert payload["tool_choice"] == "none"
                assert payload["parallel_tool_calls"] is False
                assert payload["messages"][-2]["tool_calls"][0]["id"] == "call_exact_model_123"
                assert payload["messages"][-1]["tool_call_id"] == "call_exact_model_123"
                data = json.loads(payload["messages"][-1]["content"])
                assert data["items"][0]["nm_id"] == 100
                return FakeResponse({
                    "choices": [{"message": {"content": "Проверьте товар 100: скидка 18%, предел 10%."}}],
                    "usage": {"prompt_tokens": 46, "completion_tokens": 12},
                })
            @staticmethod
            def assert_no_secrets(payload, foreign):
                serialized = json.dumps(payload, ensure_ascii=False)
                assert foreign not in serialized
                assert "987654321" not in serialized
                assert "synthetic-not-a-real-token" not in serialized
                assert "Bearer" not in serialized
                assert "mcp:8099" not in serialized

        platform = FakeAIPlatform()
        tool_tokens = []
        async def local_mcp_transport(token, name, arguments):
            principal = verify_store_token(token)
            assert principal == {"user_id": self.seller.pk, "store_id": self.store.pk}
            assert name == "wb_products" and arguments == {"limit": 1}
            tool_tokens.append(token)
            return {
                "marketplace": "wb", "items": [
                    {"nm_id": 100, "current_discount_pct": 18, "allowed_discount_pct": 10},
                ], "source": "NoSale cached WB observations",
            }

        with patch("guard.ai_gateway.build_opener", return_value=platform), \
             patch("nosale_mcp.client._call_tool", new_callable=AsyncMock,
                   side_effect=local_mcp_transport) as transport, \
             patch("guard.views.WBClient") as wb:
            response = self.ask()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(platform.requests), 2)
        self.assertEqual(transport.await_count, 1)
        self.assertEqual(len(tool_tokens), 1)
        self.assertTrue(platform.requests[0].full_url.startswith("https://platform.svdev.pro/"))
        self.assertEqual(platform.requests[0].get_header("X-ai-project-key"), "nosale")
        self.assertEqual(AssistantMessage.objects.get().mode, "smart")
        row = AssistantAIRequest.objects.get()
        self.assertEqual((row.status, row.reserved_kopeks, row.input_tokens, row.output_tokens),
                         ("success", 200, 77, 20))
        wb.assert_not_called()

    def test_billing_cap_prevents_two_call_session_before_model(self):
        self.ai.monthly_budget_kopeks = 100  # below 2x per-call reserve
        self.ai.save(update_fields=["monthly_budget_kopeks"])
        with patch("guard.smart_assistant.gateway_choose_tool") as chooser, \
             patch("guard.smart_assistant.call_owned_tool") as tool:
            self.assertEqual(self.ask().status_code, 302)
            chooser.assert_not_called()
            tool.assert_not_called()
        self.assertFalse(AssistantAIRequest.objects.exists())
