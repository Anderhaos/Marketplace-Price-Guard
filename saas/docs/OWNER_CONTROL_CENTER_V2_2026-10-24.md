# NoSale Owner Control Center v2 (2026-10-24)

## Scope
Replace the default owner landing experience at `/admin/` with a responsive dark owner console, while keeping Django Admin under existing model URLs as an advanced fallback.

Primary routes:
- `/admin/` — local DB/config overview, no external provider calls.
- `/admin/control/ai/` — AI Platform project key, explicit verified catalog read, assistant model and budgets.
- `/admin/control/mcp/` — bundled WB/Ozon adapter activation and read-only tool policy.
- `/admin/control/operations/` — global monitoring and notification controls.
- `/admin/control/tariffs/` — Standard and Smart current prices and availability for new purchases.
- `/admin/integrations/` — retained read-only diagnostics.

## Security and product invariants
- Every console endpoint is wrapped in `admin.site.admin_view`, including real PIN login + session-bound owner email verification. No separate sessions, API credentials, superuser bootstrap or backdoors.
- All mutations use POST and Django CSRF enforcement, exact allowlisted actions and model form validation. Unknown panels or sections cannot write. One POST changes only one model.
- Real database values feed overview. No claim that WB, Ozon, billing, AI, or push gateways are live just because a DB flag is enabled.
- Only the fixed first-party WB read-only adapter and Ozon placeholder may be toggled. Ozon API and arbitrary MCP URLs are not implemented.
- Credentials are Fernet-encrypted by existing gateway form, never echoed back, logged or included in audit change messages. Catalog verification makes an outbound request **only** upon explicit owner POST.
- Model identifiers are validated against previously verified catalog when available; Smart is not advertised as usable unless model, budget, token and verification all pass existing runtime checks.
- Background WB control can affect every seller, so destructive-impact warning is prominent. Existing marketplace write and bank constraints remain enforced in server-side modules.
- Every successful settings update creates a Django Admin LogEntry; no raw secrets or client payloads in audit text.
- Missing AI/gateway settings are created only by authorized owner posting a validated form. Tariffs are created with server-recognized Standard/Smart slugs and owner-supplied positive RUB price. Missing MCP/operations singletons fail closed as migration defects rather than silently reinitialize the security policy.

## Deployment
One task branch from the current main, mandatory CI (SQLite + PostgreSQL + Docker), merge into main, deploy only the exact green main SHA. Back up both E5 PostgreSQL databases and validate `pg_restore -l` before restarting respective Compose projects. Keep production and isolated preview databases separate. Verify authenticated dashboard and one authorized setting update in synthetic automated tests, then public unauthenticated routes, container health, protected routes and specific revision.

Avoid extra production data mutations (model selections, budgets, toggles) during deploy acceptance. Do not publish AI/marketplace tokens.
