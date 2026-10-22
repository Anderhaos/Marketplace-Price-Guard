# MPNO Sale SaaS — independent bootstrap

This is a copy of the working Django WB portal, NOT a replacement.
- Legacy prod remains at mpnosale.svdev.pro, /srv/mpnosale, port 8891, SQLite.
- SaaS has a separate Django runtime, Postgres volumes, secrets and port 8892.
- No legacy credentials, seller tokens or database data have been copied.
- The copied WB adapter and UI include current local fixes; acceptance is still required.
- Access grants follow the BeClients pattern and are the single source of access truth.
- All WB traffic defaults OFF via SAAS_WB_NETWORK_ENABLED=0. Even after enabling, no network access without an unexpired access grant.
- Payment webhook, OAuth, Ozon, PWA, email/PIN and notifications are not yet implemented.

To run tests locally: from saas/ set DJANGO_DEBUG=1 and SAAS_TEST_SQLITE=1, then python manage.py test.
Docker: copy .env.example to .env, generate DIFFERENT secrets and DB password, configure email or set REQUIRE_EMAIL_VERIFICATION=0 only in isolated testing, then docker compose config and docker compose up -d --build.
The existing legacy compose.portal.yml MUST NOT be changed. No live WB tokens in staging.
