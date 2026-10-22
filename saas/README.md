# MP NoSale SaaS — isolated multi-seller staging

## Isolation and entrypoints

- Legacy customer production: `https://mpnosale.svdev.pro`, E5 Docker project `mpnosale`, localhost port 8891, existing SQLite. **Do not modify, migrate or redirect it**.
- New SaaS staging: `/srv/mpnosale-saas/source/saas`, Docker project `mpnosale-saas`, localhost port 8892, isolated PostgreSQL, database/backups/keys.
- Production nginx currently routes the public `mpnosale.svdev.pro` **only to legacy 8891**; the SaaS endpoint 8892 is local-only until the deployment boundary is formally approved.
- Legacy seller credentials, tokens and databases are not copied into SaaS.

## Email OTP and PIN (2026-10-22)

Set `SAAS_PIN_AUTH_ENABLED=1`, `REQUIRE_EMAIL_VERIFICATION=1`,
`DJANGO_EMAIL_BACKEND=portal.email_backend.RelayEmailBackend`,
`MAIL_RELAY_URL=https://mail.beclients.ru/svdev-relay.php`,
`MAIL_RELAY_TOKEN=<independent secret from /etc/svdev/mail-relay/mpno.token>`,
`SMTP_FROM=MP NoSale <noreply@svdev.pro>`.

This reuses the REG.RU shared mail infrastructure but **not BeClients credentials, mailbox identity or application**. The HTTPS PHP handler is separately secured, fixed sender `noreply@svdev.pro`, fixed envelope sender, allowlisted E5 IP. The same authenticated handler serves SVDEV Gate with a **different token**. The domain `mail.svdev.pro` has an HTTPS TLS mismatch and MUST NOT be used until separately configured. Direct SMTP from E5 is not reachable.

New registration: email -> 6-digit code (10-minute TTL, 5 attempts, 60s resend cooldown) -> 6-digit PIN -> authenticated SaaS dashboard. PIN login has database-backed five-attempt lockout for 15 minutes. Forgotten PIN: email -> code -> new PIN. Challenges and lockouts persist in SaaS Postgres across containers. No direct fallback to password-only registration when PIN auth is enabled; old verification links return 410. The feature toggle defaults OFF to avoid altering existing staging until tested.

Web Push remains an independent delivery channel; not a replacement for email codes. WB network calls default OFF unless an access grant and explicit network enabling both exist.

## Verification/deploy

From repo checkout: `cd saas`; for a test-only environment use `DJANGO_DEBUG=1 SAAS_TEST_SQLITE=1` with a **test-only** Fernet `TOKEN_ENCRYPTION_KEY`; run `python manage.py test`. Check migrations with `python manage.py makemigrations --check --dry-run`.

After tests, on E5 deploy the exact GitHub branch SHA to `/srv/mpnosale-saas/source` and build **only SaaS** via `docker compose -f saas/compose.yml --project-directory saas up -d --build web` (from repo root with an explicitly correct project directory), keeping both legacy and shared E5 gateway untouched. Confirm local `http://127.0.0.1:8892/health/` is HTTP 200 and new registration/login/recovery pages display. Confirm authenticated no-send relay probe, real test mail delivery and SaaS Django email backend. Check `docker compose -f compose.yml ps` from `saas/`.

Do not make a public SaaS cutover until the public host/product identity, nginx auth throttling and acceptance are explicitly accounted for.

## Current non-OTP scope

Isolated multi-store seller guard uses PostgreSQL, WB adapter with network OFF by default, access grants, PWA, notifications and Web Push. Payment webhook, OAuth, Ozon and paid access activation still need separate acceptance. They are not implicitly complete because email auth works.
