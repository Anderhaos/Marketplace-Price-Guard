"""NoSale -> AI Platform Gateway (OpenAI-compatible /v1/chat/completions).

Never calls providers directly and never follows redirects to an arbitrary host.
Auth is provided by the encrypted owner connection or server override, never the seller browser.
"""
import json
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from billing.ai_platform_connection import runtime_gateway, project_headers


class AIGatewayUnavailable(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def is_gateway_configured():
    return runtime_gateway() is not None


def gateway_chat(*, model_id, messages, max_output_tokens, timeout_seconds):
    connection = runtime_gateway()
    if not connection:
        raise AIGatewayUnavailable("NoSale AI Platform integration is not configured")
    base_url, token = connection
    url = base_url + "/chat/completions"
    body = {
        "model": model_id,
        "messages": messages,
        "stream": False,
        "max_tokens": min(800, max(1, int(max_output_tokens))),
        "temperature": 0.2,
    }
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 14000:
        raise AIGatewayUnavailable("AI context exceeds the allowed size")
    request = Request(url, data=encoded, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        **project_headers(),
    })
    try:
        # Refuse proxies and redirects; HTTPError includes 3xx/4xx/5xx.
        with build_opener(ProxyHandler({}), NoRedirect()).open(
            request, timeout=min(30, max(2, int(timeout_seconds)))
        ) as response:
            if response.status != 200:
                raise AIGatewayUnavailable("AI Platform did not return HTTP 200")
            raw = response.read(32_769)
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        raise AIGatewayUnavailable("AI Platform request failed") from exc
    if len(raw) > 32768:
        raise AIGatewayUnavailable("AI Platform response too large")
    try:
        payload = json.loads(raw)
        answer = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIGatewayUnavailable("Invalid AI Platform response") from exc
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 1800:
        raise AIGatewayUnavailable("Invalid AI assistant answer")
    usage = payload.get("usage") or {}
    return answer.strip(), {
        "input_tokens": usage.get("prompt_tokens") if type(usage.get("prompt_tokens")) is int else 0,
        "output_tokens": usage.get("completion_tokens") if type(usage.get("completion_tokens")) is int else 0,
    }
