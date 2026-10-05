# MP No Sale portal

The portal lives in `portal/` and `guard/`. The old `app/` directory remains a local, single-seller prototype and is not started by the portal deployment.

## Seller flow

1. Register with email. Email confirmation is required when SMTP works; the E4 launch temporarily sets `REQUIRE_EMAIL_VERIFICATION=0` because the existing `svdev.pro` SMTP server is unreachable from E4.
2. Sign in and enter a Wildberries Prices and Discounts API token in Settings.
3. Run a manual check to load products and discounts. It is read-only while automatic correction is disabled.
4. Set the default permitted discount, interval, and maximum fixes. Individual products can override the discount.
5. Enable automatic correction. The separate worker checks all WB product pages, submits discount-only updates, and records upload IDs. A submitted upload is not reported as successful until WB confirms its final status.

The current WB integration monitors the seller discount field. WB Club and marketplace-funded promotional discounts are separate fields and are not changed by this rule.

The built-in Django `/admin/` is for site staff only. Sellers use the dashboard and cannot access another account's records.

## Local development

Use Python 3.12+ and install `requirements.txt` in a virtual environment. Set environment variables from `portal.env.example`, with `DJANGO_DEBUG=1` and secure cookies/SSL redirect disabled for local HTTP. Generate secrets:

```sh
python -c 'import secrets; print(secrets.token_urlsafe(64))'
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Then run:

```sh
python manage.py migrate
python manage.py configure_sqlite
python manage.py runserver
python manage.py run_guard
```

The worker is a separate long-running process. Run one worker per SQLite database. With `REQUIRE_EMAIL_VERIFICATION=1`, registration sends a confirmation email; the console backend is suitable only for local development.

## E4 deployment

The intended deployment is one Docker Compose project on E4, with `compose.portal.yml`, one web container, one worker container, one backup container, and named SQLite data and backup volumes. The web container listens on host `127.0.0.1:8891`. Nginx serves `mpnosale.svdev.pro` over HTTPS and proxies to that port. Keep the database volume on E4's local disk, never on an NFS/SMB mount.

Before bringing up the service, create a private `portal.env` from `portal.env.example`. Generate independent `DJANGO_SECRET_KEY` and `TOKEN_ENCRYPTION_KEY` values. E4 currently uses `REQUIRE_EMAIL_VERIFICATION=0`; email password reset is disabled in that mode. Once SMTP connectivity is repaired, set `REQUIRE_EMAIL_VERIFICATION=1` and verify delivery before restarting the web service. Keep `TOKEN_ENCRYPTION_KEY` in a separate secure backup, since losing it makes seller tokens unreadable. Create the staff account with `python manage.py createsuperuser` inside the web container. Ordinary registrations never receive staff access.

Deployment commands from the project directory:

```sh
docker compose -f compose.portal.yml up -d --build
docker compose -f compose.portal.yml ps
docker compose -f compose.portal.yml logs --tail=100 web worker
```

The E4 Nginx files are `deploy/nginx-mpnosale.conf` and `deploy/nginx-rate-limit.conf`. The first goes in `/etc/nginx/sites-enabled/mpnosale.svdev.pro`, the second in `/etc/nginx/conf.d/mpnosale-rate-limit.conf`. Run `nginx -t` before reloading. The TLS certificate for `mpnosale.svdev.pro` is issued with Certbot on E4; renewals are handled by its scheduled task.

The backup service uses SQLite's online backup API daily and retains 14 snapshots in a separate Docker volume. Copy those snapshots and the private `portal.env`/encryption key to a secure offsite destination. Do not copy only `portal.sqlite3` while WAL writes are in progress. Test a restore before accepting seller tokens. Ensure the backup destination is not public.

Public launch checks: HTTPS certificate valid; registration and login work; with email verification enabled, a confirmation email arrives and activates the account; different sellers cannot see each other's products; token validation works; a test WB account shows every product page; the worker records a submitted upload and later a confirmed or failed outcome. Start with one test seller and the default disabled autoguard, then enable it explicitly.
