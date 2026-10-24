# NoSale owner background-operation controls (R4.2, 2026-10-24)

Under parent #38, issue #41.

## Owner versus seller

One owner-only admin: `https://nosale.svdev.pro/admin/`, unchanged PIN/email authentication, **active superuser required**. The seller portal remains only seller-owned settings. A seller may choose an interval or notification preferences for its own shop but cannot override global limits or pause/resume the fleet.

`OwnerOperationsSettings(pk=1)`, stored in PostgreSQL and seeded by migration `billing.0010`, is editable by the SaaS owner only under **Фоновые процессы SaaS**. Django's immutable LogEntry audit logs owner/changed field names, never token values. Singleton cannot be deleted. No editable .env or code changes needed for operational toggles and polling intervals.

## Controls and safeguards

- `guard_checks_enabled`: stops picking up new seller accounts in `run_due_checks()`. Each queued seller is rechecked before claiming a job. A request **already running** is not forcibly cancelled. Default ON, **turning it off pauses price/discount protection globally**. The separate hard `SAAS_WB_NETWORK_ENABLED`, `SAAS_WB_READ_ONLY`, JWT rights, quotas, and seller authorization cannot be lifted via admin.
- `guard_poll_seconds`: 5–60 seconds (default 10), controls idle worker cycle sleep. Changing this takes effect on the **next** loop without restart. This is NOT the rate/interval of individual seller WB API calls (seller/base-token constraints remain independently enforced).
- `instant_events_enabled`: when OFF, new monitoring events remain in local notification feed, but no new instant Web Push delivery records are generated. They are not automatically sent later after re-enabling.
- `daily_digests_enabled`: when OFF, no new daily digest is created; existing records aren't deleted. Re-enabling on the same day can generate a digest if none has yet been created.
- `push_delivery_enabled`: pauses sending all queued PushDelivery records without deleting them. Resuming sends pending records, subject to existing TTL, retries, subscription validity, rate limits and endpoint allowlist. The in-flight send already begun cannot be cancelled.
- `notifications_poll_seconds`: 15–300 seconds (default 30), next idle worker loop.
- `push_batch_size`: 1–20 (default 20), upper bound for a single dispatch cycle. Existing anti-replay lease, endpoint allowlist, and retry backoff unchanged.
- If the owner policy record is absent, worker Guard and Push network work fails closed (not silently enabled); normal guard/encryption/AI/payment safety limits stay intact.
- Settings are re-read between seller scans / device sends. **Do not** imply a UI toggle can instantly cancel an already running HTTP operation.

## Acceptance and rollout

Zero real WB, AI, mail, bank or Push provider requests during test. Additive migration preserves previous defaults. Tests check owner-only edit and audit, unavailable direct admin URLs for staff/seller, queue pause without message loss, rate-limited batch, changed worker intervals, no token decryption on stopped WB cycles and read-only operational state in `/admin/integrations/`.

Ship only from one branch → green CI → full PostgreSQL suite, backup both SaaS and preview → main merge → exact verified SHA deployed to isolated E5 and SSH-only preview; legacy customer remains untouched.

Out of scope: external MCP registration, real Ozon API, AI provider enrollment/billing, owner account provisioning, payment enablement. Parent #38 remains open.
