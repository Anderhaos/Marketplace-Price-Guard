# Reuse inventory and next tasks

Copied from working Marketplace Price Guard: guard/wb.py, guard/monitor.py, guard/views.py, Django UI and confirmation flow.
Reused BeClients proven concept: access grants are authoritative rather than subscription/payment status (backend/src/modules/billing/billing.service.ts).
To port with tests: BeClients billing/tochka.service.ts, billing/tochka-webhook.service.ts, billing/billing-reconcile.job.ts. TypeScript cannot be reused directly in Django. Do not enable payments without verified signature, operation id, amount and merchant matching, idempotency and reconciliation.
Next: WB commercial API authorization, safe multi-store schema, Ozon adapter, email code+PIN, PWA and notification center, daily digest, CI/staging and restore test.
