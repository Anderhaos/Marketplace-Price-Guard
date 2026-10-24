# NoSale owner console R4.1 — readiness and audit

**Implemented for issue #39, parent #38.** Owner controls use the existing `/admin/` and email/PIN login. Seller accounts and ordinary staff receive HTTP 403. No alternative owner login, public service endpoints or extra passwords.

## Page `/admin/integrations/`

- Read-only overview of local configuration: official WB direct-network gate and read-only flag, installed MCP enabled tools and limits, Ozon **not implemented**, independently enrolled NoSale AI Platform project (encrypted key exists, last tested success/date, selected model in verified catalog, owner's budget and AI toggle), payment/fiscalization deployment gates, active tariff offers, PIN/email transport, user/store counts and undelivered push count.
- Does **not** contact WB, Ozon, MCP, AI Platform, payment provider or mail/push APIs at page load. Thus `last_check_ok` means **last verification result, not real-time uptime**; `enabled_by_deploy` is merely a gate, not proof of safe/approved payment collection.
- No key/token bytes, environment values, seller item data or arbitrary provider messages are rendered. Only selected model ID and bounded safe status details; no external links, arbitrary remote MCP URL, user-selectable endpoint, provider-secret storage.
- Links go to **existing owner-editable PostgreSQL-backed** MCP settings, AI Platform encrypted service key and separate explicit verification action, model/budget limits, tariffs, user management. Technical authorization, fixed host allowlists, security and legal/payment gates remain hard-enforced in code/deploy and are not silently made into owner overrides.

## Audit

- The built-in Django `LogEntry` (not another parallel ledger) records authenticated admin changes, with action timestamp, owner username, target object and changed **field names**, never prior/new values.
- A read-only superuser-restricted admin changelist `/admin/admin/logentry/` shows entries only for NoSale owner settings, MCP policy, AI Platform project settings and tariff offers. It is not an API for seller data or admin log mutation.
- Seller/customer/staff denied on both status and journal; account role is checked by the existing owner-only admin view gate.

## Remaining owner R4 items

- No active privileged owner account yet: must complete existing authenticated email/PIN registration and explicit verified owner provisioning. Never auto-grant privileges from guessed email or a UI input.
- Live AI Platform NoSale project verification (#47 in AI Platform), model function calling and provider cost data remain outside this local status page.
- Registry-driven onboarding for additional third-party MCP/providers, Ozon real APIs, true payment fiscalization acceptance and operational monitoring controls require independently approved integrations under #38. Do not claim they work based on the read-only status UI.
- Any new operating control must be owned and audited in PostgreSQL without compromising fixed security invariants; keep seller-specific controls in seller portal.

**Acceptance:** all GitHub CI checks + PostgreSQL 286+ regression, owner/staff/seller role tests, no-network and secret redaction, audit immutable and filtered, checked migration state, backed-up E5 SaaS and preview deployment from one merged main SHA, legacy healthy.
