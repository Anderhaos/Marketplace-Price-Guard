"""Trusted backend-to-backend MCP client for NoSale Smart.

The LLM never receives the bearer, store/user IDs, endpoint or raw auth details.
The selected store must originate in NoSale's authenticated Django session.
"""
import asyncio
import json

import httpx2
from django.db import close_old_connections, connections
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from .auth import RESOURCE, issue_store_token

# Fixed read-only tools. Namespaces are independent per marketplace.
# All argument bounds are enforced here AND in MCP server adapters.
TOOL_LIMITS = {
    "wb_overview": {},
    "wb_products": {"limit": (1, 20), "offset": (0, 1000)},
    "wb_events": {"limit": (1, 20)},
    "wb_promotions": {"limit": (1, 15)},
    "ozon_adapter_status": {},
}
MAX_TOOL_RESULT_BYTES = 5500


class MCPToolUnavailable(Exception):
    pass


def validated_request(name, arguments):
    """Ignore no model-controlled identities or endpoints: strictly reject them."""
    if not isinstance(name, str) or name not in TOOL_LIMITS:
        raise MCPToolUnavailable("Model selected an unavailable MCP tool")
    if not isinstance(arguments, dict) or any(k not in TOOL_LIMITS[name] for k in arguments):
        raise MCPToolUnavailable("MCP tool arguments are not allowed")
    clean = {}
    for key, raw in arguments.items():
        low, high = TOOL_LIMITS[name][key]
        if type(raw) is not int or not low <= raw <= high:
            raise MCPToolUnavailable("MCP tool arguments exceed allowed bounds")
        clean[key] = raw
    return name, clean


def definitions(marketplace):
    """Compact OpenAI tool specification; no parameters for user/store IDs."""
    names = ("ozon_adapter_status",) if marketplace == "ozon" else (
        "wb_overview", "wb_products", "wb_events", "wb_promotions",
    )
    descriptions = {
        "wb_overview": "Summary of saved WB product discounts, guard limits and freshness; not live WB.",
        "wb_products": "Read seller's cached WB products, prices and discount limits.",
        "wb_events": "Read seller's WB change-event kinds and times; no event payload.",
        "wb_promotions": "Read saved WB promotion audit, risk signals, unknown financing.",
        "ozon_adapter_status": "Ozon is not connected yet; return not_implemented status.",
    }
    items = []
    for name in names:
        props = {key: {"type": "integer", "minimum": lo, "maximum": hi}
                 for key, (lo, hi) in TOOL_LIMITS[name].items()}
        items.append({
            "type": "function", "function": {
                "name": name, "description": descriptions[name],
                "parameters": {"type": "object", "properties": props,
                               "additionalProperties": False},
            },
        })
    return items


async def _call_tool(token, name, arguments):
    # Internal hostname/port/resource are immutable; no redirects/proxies.
    async with httpx2.AsyncClient(
        headers={"Authorization": "Bearer " + token},
        timeout=httpx2.Timeout(9.0, read=10.0),
        follow_redirects=False, trust_env=False,
    ) as http:
        async with Client(streamable_http_client(RESOURCE, http_client=http)) as client:
            response = await client.call_tool(name, arguments)
            if response.is_error:
                raise MCPToolUnavailable("MCP denied access or read failed")
            payload = response.structured_content
            if not isinstance(payload, dict):
                # MCP clients can fall back to a single canonical JSON TextContent.
                content = response.content
                if len(content) != 1 or getattr(content[0], "type", None) != "text":
                    raise MCPToolUnavailable("Unexpected MCP response")
                try:
                    payload = json.loads(content[0].text)
                except (TypeError, ValueError) as exc:
                    raise MCPToolUnavailable("Unexpected MCP response format") from exc
            if not isinstance(payload, dict):
                raise MCPToolUnavailable("Unexpected MCP tool data")
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > MAX_TOOL_RESULT_BYTES:
                raise MCPToolUnavailable("MCP result exceeds the safe context limit")
            return payload


def call_owned_tool(user, account, name, arguments):
    """Sync Django entrypoint; always validates ownership and active plan first."""
    name, arguments = validated_request(name, arguments)
    if account.marketplace == "wb" and name == "ozon_adapter_status":
        raise MCPToolUnavailable("Ozon tool cannot run on a selected WB store")
    if account.marketplace == "ozon" and name != "ozon_adapter_status":
        raise MCPToolUnavailable("WB tools cannot run on a selected Ozon store")
    try:
        # Minted only after request has selected a seller-owned store; never log.
        token = issue_store_token(user, account)
        return asyncio.run(_call_tool(token, name, arguments))
    except MCPToolUnavailable:
        raise
    except Exception as exc:
        # Do not leak internal endpoint, secret token or provider error to UI.
        raise MCPToolUnavailable("Internal MCP tool is unavailable") from exc
    finally:
        close_old_connections()
