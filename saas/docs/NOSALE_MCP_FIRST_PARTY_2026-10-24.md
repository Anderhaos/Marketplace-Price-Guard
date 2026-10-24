# NoSale MCP Server — internal first-party architecture (2026-10-24)

## Decision

One private MCP server for **all** marketplaces; independent adapters under `nosale_mcp/adapters/`.
Initial Wildberries adapter uses **existing cached NoSale observations** and seller-scoped DB queries. Ozon adapter reports `not_implemented`; it never returns fake seller data. No 3rd-party MCP is installed.

The ordinary NoSale WB Guard keeps its **direct WB API** adapter and background scheduler. A failure of MCP or AI does not affect price protection.

## Runtime

- The official Python MCP SDK **v2** supplies Streamable HTTP at `http://mcp:8099/mcp` inside each Compose project.
- `saas/compose.yml` and `saas/deploy/ui-preview.compose.yml` add a dedicated `mcp` container. **No host ports and no nginx route**. Docker's `mcp` hostname is visible only to the matching project network, not other projects or the internet.
- `Dockerfile.mcp` uses a dedicated SDK dependency layer; the existing web/worker images remain without SDK dependency.
- The only bearer allowed comes from `nosale_mcp.auth.issue_store_token(authenticated_user, server_selected_store)` in trusted NoSale code. It is signed by Django's production `SECRET_KEY`, includes fixed MCP resource audience, seller+selected-store identity, expires in 90 seconds. **Do not expose to browser, chat text, logs or LLM.** No tool takes a model-provided `user_id`, `store_id`, `token`, or endpoint.
- The MCP transport performs Authorization Bearer validation. **Every call rechecks** active seller user, owned store and subscription/grant. Revoking a plan or removing an account immediately revokes the tools even if a signed bearer has seconds left.
- SDK HTTP resource metadata does not imply public OAuth self-service. This release deliberately exposes **no external OAuth flow**; the internal backend alone is the authorized token issuer. External AI Platform clients cannot connect to the private Docker network until a separate user-consent and delegated OAuth/gateway design is accepted.

## Tools (ALL READ-ONLY)

| Tool | Adapter | Data, limit |
|---|---|---|
| `marketplace_capabilities` | Registry | WB operational; Ozon planned |
| `seller_stores` | Core | Own stores only, max 20, selected ID |
| `wb_overview` | WB | Deterministic discount Guard recommendations from saved products |
| `wb_products(limit,offset)` | WB | Max 50 products, offset bound 1000; price/limit/timestamp only |
| `wb_events(limit)` | WB | Max 50 event kinds and timestamps, never free-form raw event messages |
| `wb_promotions(limit)` | WB | Max 30 saved promo summaries; platform funding and profitability unverified |
| `ozon_adapter_status` | Ozon | Honest `not_implemented`, no API calls |

**Not exposed:** all seller credentials, tokens, unsafe WB endpoints, financing assertions, uploads, refund/payment, price changes, subscription management, other stores' data. There is no tool that refreshes promotions (quota-controlled seller UI owns that operation).

## Integration with NoSale Smart

This milestone delivers the actual protected **MCP protocol/server** and deterministic adapters. The already existing Smart AI Platform connection is not yet enrolled on E5. Do not route tokens or seller data to external AI until a verified service model, per-user tool approvals, cost guard and project scopes are accepted under R3/R4 issues #10/#11. Implementing model tool-call orchestration will reuse this standard internal MCP endpoint with authenticated per-request context. No LLM is needed for listing products or enforcing discount limits.

The MCP can also be called by other **trusted** internal application functions, but a raw HTTP request without a scoped bearer returns HTTP 401. Never publish `mcp:8099` or forward it to a browser.

## Acceptance

- Signed token audience, TTL, tampering, user-store binding, subscription revocation.
- Tenant-specific products, promotions and event metadata; no raw messages or passwords.
- Explicit WB read-only tool list, Ozon adapter placeholder, no WB network.
- CI runs `manage.py test guard billing push nosale_mcp` with official MCP SDK and builds both Docker images.
- PostgreSQL integration and real MCP endpoint smoke on E5: unauthenticated HTTP 401, authorized tools via a backend-minted ephemeral bearer on the internal Compose network (tokens never displayed or stored).
- Release source: **merge into main → green main CI → verified staging backup → deploy immutable SHA**, legacy seller excluded.

Commercial gate: #12 remains open. The MCP endpoint must not be exposed to internet or AI Platform until a separate secure delegation task is complete.
