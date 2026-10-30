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


# Chat-facing Smart integration was retired; provider wire contracts below
# remain useful. Scheduled narration is tested in test_smart_scheduled.py.


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
