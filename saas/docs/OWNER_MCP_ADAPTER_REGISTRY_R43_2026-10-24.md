# NoSale Owner MCP Adapter Registry — R4.3 (2026-10-24)

Issue #44 under parent #38.

## Design and access

There is exactly **one** internal NoSale MCP v2 server. WB and Ozon are independent reviewed **first-party adapters**, not separately hosted MCP instances. SaaS owner manages them under `/admin/billing/ownermcpadapter/`, protected by verified email/PIN + active superuser; ordinary staff and sellers cannot see/modify. This is distinct from seller-store settings.

`billing.OwnerMCPAdapter` is a PostgreSQL registry seeded in additive migration `0011`:
- `wb`: enabled by default; read-only cached WB observations; no direct WB API calls through MCP.
- `ozon`: enabled by default **for status only**; it explicitly returns `not_implemented`, no API, no inventory, no sale promises.
- A reviewed adapter's `enabled` switch can be changed by owner. Immutable slug/driver, no free-form URLs, no create/delete and no keys. Django admin logs the owner and changed field names.
- Existing `OwnerMCPSettings` retains per-tool switches and limits. An MCP call succeeds only if BOTH **adapter registry** and **global/per-tool policy** permit it, on BOTH trusted Django client and actual MCP server invocation. No implicit fallback to a third-party host. A removed/missing row denies access.
- Runtime checks query PostgreSQL rather than loading a config file or restarting the server. Disabling WB adapter prevents WB MCP reads only: the separate direct WB Guard price protection remains governed by existing seller permissions and global guards.
- The MCP SDK tool catalog may still enumerate registered tool names, but disabled tools **reject execution**, and Smart's offered tool list only includes permitted tools. The `marketplace_capabilities` tool reports effective permission. Do not describe SDK catalog as dynamically removed if not implemented.
- Changing the registry cannot enable marketplace writes, expose private Docker MCP service publicly, mint bearer tokens, connect external hosts, install code, or enable real Ozon API.

## Subsequent provider/connector onboarding

Third-party MCP installation and secure URL/credential registration are **not** implemented here. That requires a separately reviewed adapter driver, HTTPS-only host allowlist and outbound firewall rules, DNS rebinding/redirect/SSRF protections, credential encryption, explicit user consent, OAuth/delegation or equivalent per seller, safe tool metadata/output validation and audit. Do not accept a user-entered URL without such controls.

## Acceptance

- GitHub PR checks and full isolated E5 PostgreSQL suite pass; `makemigrations --check` clean.
- With a disposable signed seller context on isolated preview, disabling WB adapter via owner form prevents further valid-bearer WB MCP tool calls; Ozon remains status-only. Enabling restores permitted reads, and the original policy is restored.
- Seller/staff requests to registry URLs result in HTTP 403. Owner admin audit gets one field-change entry without secrets; no extra network traffic on readiness GET.
- Back up both SaaS and preview PostgreSQL; deploy immutable merged `main` SHA to both environments, ensure old legacy production isn't changed. No active owner is provisioned automatically.

Still open: parent #38, owner enrollment #10, live Ozon API/AI/merchant gates and complete commercial release #12.
