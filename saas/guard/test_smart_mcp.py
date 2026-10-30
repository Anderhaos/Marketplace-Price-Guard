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


# Legacy model-led MCP interaction through seller chat was retired.
# Read-only MCP wire, schema and safety contracts above remain covered.
