"""NoSale -> AI Platform Gateway (OpenAI-compatible /v1/chat/completions).

Never calls providers directly and never follows redirects to an arbitrary host.
All auth is provided by the server environment, not by seller browser requests.
"""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from django.conf import settings


class AIGatewayUnavailable(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def is_gateway_configured():
    return bool(
        getattr(settings, "NOSALE_AI_GATEWAY_URL", "")
        and getattr(settings, "NOSALE_AI_SERVICE_TOKEN", "")
    )


def _gateway_url():
    url = getattr(settings, "NOSALE_AI_GATEWAY_URL", "")
    parsed = urlparse(url)
    host = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError as exc:
        raise AIGatewayUnavailable("Invalid AI gateway port") from exc
    if (parsed.scheme != "https" or parsed.username or parsed.password or port
            or parsed.query or parsed.fragment or parsed.path != "/v1/chat/completions"
            or not host.endswith(".svdev.pro")):
        raise AIGatewayUnavailable("AI Platform endpoint is not trusted")
    return url


def gateway_chat(*, model_id, messages, max_output_tokens, timeout_seconds):
    if not is_gateway_configured():
        raise AIGatewayUnavailable("NoSale AI Platform integration is not configured")
    url = _gateway_url()
    token = settings.NOSALE_AI_SERVICE_TOKEN
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
