"""NoSale -> AI Platform Gateway (OpenAI-compatible /v1/chat/completions).

Never calls providers directly and never follows redirects to an arbitrary host.
Auth is provided by the encrypted owner connection or server override, never the seller browser.
"""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from billing.ai_platform_connection import runtime_gateway, project_headers


class AIGatewayUnavailable(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def is_gateway_configured(model_id=None):
    return runtime_gateway(model_id=model_id) is not None


def _validated_tool_names(tools):
    if not isinstance(tools, list) or not (1 <= len(tools) <= 5):
        raise AIGatewayUnavailable("Invalid AI tool allowlist")
    names = set()
    for item in tools:
        if not isinstance(item, dict) or item.get("type") != "function":
            raise AIGatewayUnavailable("Invalid AI tool definition")
        fn = item.get("function")
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
            raise AIGatewayUnavailable("Invalid AI tool definition")
        name = fn["name"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name) or name in names:
            raise AIGatewayUnavailable("Invalid or duplicate AI tool name")
        names.add(name)
    return names


def gateway_chat(*, model_id, messages, max_output_tokens, timeout_seconds, tools=None, thinking_mode=None):
    connection = runtime_gateway(model_id=model_id)
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
    if thinking_mode is not None:
        # Short scheduled narration must not spend output tokens on the
        # default-high DeepSeek reasoning mode. See official Thinking Mode API.
        if thinking_mode != "disabled" or not model_id.startswith("deepseek/"):
            raise AIGatewayUnavailable("Unsupported thinking mode")
        body["thinking"] = {"type": "disabled"}
    if tools is not None:
        # Follow-up tool-result rounds must send original tool definitions,
        # while disallowing further model tool requests (one tool max).
        _validated_tool_names(tools)
        body["tools"] = tools
        body["tool_choice"] = "none"
        body["parallel_tool_calls"] = False
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


def gateway_choose_tool(*, model_id, messages, tools, timeout_seconds):
    """One OpenAI-compatible function-selection round through verified AI Platform.

    Strictly bounded tool metadata, output and count. No external MCP address or
    seller bearer is included in any model messages or tool specification.
    """
    connection = runtime_gateway(model_id=model_id)
    if not connection:
        raise AIGatewayUnavailable("NoSale AI Platform connection not verified")
    if not isinstance(tools, list) or not (1 <= len(tools) <= 5):
        raise AIGatewayUnavailable("Invalid AI tool allowlist")
    allowed_names = _validated_tool_names(tools)
    base_url, token = connection
    payload = {
        "model": model_id, "messages": messages, "stream": False,
        "max_tokens": 180, "temperature": 0, "tools": tools,
        "tool_choice": "auto", "parallel_tool_calls": False,
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > 14500:
        raise AIGatewayUnavailable("Tool selection context too large")
    request = Request(base_url + "/chat/completions", data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        "Accept": "application/json", **project_headers(),
    })
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(
            request, timeout=min(20, max(2, int(timeout_seconds))),
        ) as response:
            if response.status != 200:
                raise AIGatewayUnavailable("AI Platform rejected tool selection")
            raw = response.read(32769)
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        raise AIGatewayUnavailable("AI Platform tool selection unavailable") from exc
    if len(raw) > 32768:
        raise AIGatewayUnavailable("Oversized tool selection response")
    try:
        data = json.loads(raw)
        msg = data["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        usage = data.get("usage") or {}
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise AIGatewayUnavailable("Malformed tool selection response") from exc
    if not isinstance(calls, list) or len(calls) > 1:
        raise AIGatewayUnavailable("Parallel or malformed tool calls prohibited")
    selected = None
    if calls:
        fn = calls[0].get("function") if isinstance(calls[0], dict) else None
        if not isinstance(fn, dict) or fn.get("name") not in allowed_names:
            raise AIGatewayUnavailable("AI tried to call an unapproved tool")
        arg_raw = fn.get("arguments")
        if not isinstance(arg_raw, str) or len(arg_raw) > 250:
            raise AIGatewayUnavailable("AI tool arguments invalid")
        try:
            args = json.loads(arg_raw)
        except ValueError as exc:
            raise AIGatewayUnavailable("AI tool arguments malformed") from exc
        if not isinstance(args, dict):
            raise AIGatewayUnavailable("AI tool arguments invalid")
        model_call_id = calls[0].get("id")
        if not isinstance(model_call_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", model_call_id):
            raise AIGatewayUnavailable("AI tool call ID is invalid")
        selected = {"name": fn["name"], "arguments": args, "call_id": model_call_id}
        reasoning_content = msg.get("reasoning_content")
        if reasoning_content is not None:
            if not isinstance(reasoning_content, str) or len(reasoning_content) > 6000:
                raise AIGatewayUnavailable("AI tool reasoning context invalid")
            selected["reasoning_content"] = reasoning_content
    answer = msg.get("content")
    if answer is not None and (not isinstance(answer, str) or len(answer) > 1800):
        raise AIGatewayUnavailable("Invalid preliminary AI reply")
    return selected, answer, {
        "input_tokens": usage.get("prompt_tokens") if type(usage.get("prompt_tokens")) is int else 0,
        "output_tokens": usage.get("completion_tokens") if type(usage.get("completion_tokens")) is int else 0,
    }
