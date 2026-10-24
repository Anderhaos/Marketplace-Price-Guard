"""One internal MCP endpoint for all marketplace adapters.

No public port, no dynamic tool installation, no seller credentials or WB calls.
The browser never speaks directly to this endpoint. The trusted NoSale backend
mints short-lived, audience-bound store context for each authorized request.
"""
import asyncio
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portal.settings")
import django
django.setup()

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl

from django.db import close_old_connections, connections
from .auth import RESOURCE, scope_account, verify_store_token
from .adapters import ozon, wb


class StoreTokenVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        principal = verify_store_token(token)
        if principal is None:
            return None
        return AccessToken(
            token=token,
            client_id="nosale-backend",
            subject=str(principal["user_id"]),
            scopes=["nosale:read"],
            resource=RESOURCE,
            claims=principal,
        )


mcp = MCPServer(
    "NoSale Marketplace MCP", version="0.1.0",
    instructions=(
        "Internal read-only seller data, never call external WB/Ozon API, "
        "never modify prices, actions or subscriptions. "
        "Unknown promotion funding and margin are not financial claims."
    ),
    token_verifier=StoreTokenVerifier(),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl("https://nosale.svdev.pro/"),
        resource_server_url=AnyHttpUrl(RESOURCE),
        required_scopes=["nosale:read"],
        validate_token_resource=True,
    ),
)


def _invoke(fn, *args, **kwargs):
    close_old_connections()
    try:
        return fn(*args, **kwargs)
    finally:
        connections.close_all()


def _principal():
    token = get_access_token()
    if token is None or "nosale:read" not in token.scopes:
        raise PermissionError("NoSale authenticated MCP context required")
    return token.claims


def _call(fn, **kwargs):
    return _invoke(fn, scope_account(_principal()), **kwargs)


@mcp.tool()
async def marketplace_capabilities() -> dict:
    """List marketplace adapter availability. This cannot query seller APIs."""
    claims = _principal()
    def build():
        scope_account(claims)
        return {
            "server": "NoSale MCP", "read_only": True,
            "marketplaces": {
                "wb": {"state": "available", "tools": [
                    "wb_overview", "wb_products", "wb_events", "wb_promotions",
                ]},
                "ozon": {"state": "not_implemented", "tools": []},
            },
        }
    return await asyncio.to_thread(_invoke, build)


@mcp.tool()
async def seller_stores() -> dict:
    """List only stores of the authenticated seller, max 20; never expose tokens."""
    claims = _principal()
    def collect():
        selected = scope_account(claims)
        rows = list(selected.user.seller_stores.order_by("pk").values("pk", "name", "marketplace")[:20])
        return {"selected_store_id": selected.pk,
                "stores": [{"id": row["pk"], "name": row["name"][:90],
                            "marketplace": row["marketplace"]} for row in rows]}
    return await asyncio.to_thread(_invoke, collect)


@mcp.tool()
async def wb_overview() -> dict:
    """Seller-owned WB discount guard overview from cached observations."""
    return await asyncio.to_thread(_call, wb.overview)


@mcp.tool()
async def wb_products(limit: int = 20, offset: int = 0) -> dict:
    """Read at most 50 owned WB products; never query WB or return API tokens."""
    return await asyncio.to_thread(_call, wb.products, limit=limit, offset=offset)


@mcp.tool()
async def wb_events(limit: int = 20) -> dict:
    """Read at most 50 owned WB event timestamps and kinds, no raw messages."""
    return await asyncio.to_thread(_call, wb.events, limit=limit)


@mcp.tool()
async def wb_promotions(limit: int = 15) -> dict:
    """Read last seller-owned promotion audit; funding/margin always unverified."""
    return await asyncio.to_thread(_call, wb.promotions, limit=limit)


@mcp.tool()
async def ozon_adapter_status() -> dict:
    """Report planned Ozon adapter only; do not invent Ozon inventory or prices."""
    return await asyncio.to_thread(_call, ozon.status)


if __name__ == "__main__":
    # One internal-only Docker network endpoint; no host port or external nginx.
    # Strict DNS-rebinding host allowlist (MCP v2 SDK).
    mcp.run(
        transport="streamable-http", host="0.0.0.0", port=8099,
        stateless_http=True, json_response=True, max_request_body_size=65536,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["mcp:8099", "127.0.0.1:8099", "localhost:8099"],
            allowed_origins=[],
        ),
    )
