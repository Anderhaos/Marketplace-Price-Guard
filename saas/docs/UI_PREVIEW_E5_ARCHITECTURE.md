# NoSale UI Preview — single source of truth

## Architecture (2026-10-23)

**GitHub → isolated E5 Django + PostgreSQL → restricted SSH port forwarding → Windows browser.**

- Canonical Git repository: `Anderhaos/Marketplace-Price-Guard`, UI branch `feature/nosale-welcome-email-first`, reviewed draft PR #3.
- Isolated E5 checkout: `/srv/mpnosale-ui-preview/source/saas`.
- Isolated E5 Compose project: `mpnosale-ui-preview`, in Git at `saas/deploy/ui-preview.compose.yml`.
- Isolated PostgreSQL 16 volume: `mpnosale-ui-preview_preview_pg_data`, database `nosale_ui_preview`.
- E5 web port **127.0.0.1:8894** (not exposed to the Internet, not routed through Nginx).
- Browser on Windows **http://127.0.0.1:8926/login/**. This port is an **SSH tunnel only**; no locally running Python, Django, SQLite, PostgreSQL, or frontend development server. Django renders templates directly on E5.
- SSH key stored in the real Windows user profile `%USERPROFILE%\.ssh\nosale-preview-ed25519`, with the pinned E5 host key in `known_hosts`.
- Use **Git for Windows OpenSSH**, `C:\Program Files\Git\usr\bin\ssh.exe`; the Windows System32 OpenSSH on this workstation exits 255 even with `-V`.
- Dedicated Linux account `nosale-preview-tunnel` accepts the key only for forwarding to `127.0.0.1:8894`; command execution and interactive shell are disabled.
- Windows Task Scheduler task `NoSale UI tunnel 8926`: triggers at user login; runs the tunnel with keepalives, restart on failure and failure-fast port binding. Only the user's own session can access localhost.

**Isolation invariant:** production `nosale.svdev.pro` / `8892`, real seller legacy `mpnosale.svdev.pro` / `8891` and all other applications or databases must remain untouched.

## Preview safety configuration

E5 preview uses a separate file `saas/.preview.env` (gitignored, mode 0600) containing newly generated, independent Django and PostgreSQL secrets. No production secrets are reused.

- `DJANGO_DEBUG=1` is **only** for this loopback-only, SSH-authenticated, non-production preview. Production remains `DEBUG=0`. It permits the local console email backend without weakening production's mandatory trusted relay.
- `SAAS_TEST_SQLITE` is absent; the application **always uses PostgreSQL** in preview.
- `SAAS_PIN_AUTH_ENABLED=1`, `REQUIRE_EMAIL_VERIFICATION=1`, `SAAS_ACCESS_ENFORCED=1`.
- `SAAS_WB_NETWORK_ENABLED=1`, `SAAS_WB_TOKEN_VALIDATION_ENABLED=1` enable real WB token verification and read-only inventory sync on the isolated E5 instance. `SAAS_WB_READ_ONLY=1` is MANDATORY: every write endpoint is blocked in the HTTP transport and the worker, and discount controls are disabled in the UI. Django will refuse to start a DEBUG PIN-auth deployment with WB network enabled unless read-only mode is active. `NOSALE_TOCHKA_ENABLED=0` stays unchanged.
- Preview WB tokens are encrypted and stored exclusively in its separate PostgreSQL. They are not transferred to production or committed to Git. Users must paste tokens directly into the protected preview form, **never** into conversation, console commands, logs or GitHub issues.
- Successful initial import, not merely valid token, unlocks the seller dashboard. Preview never submits discount changes; test any changes separately on an approved WB test account in a dedicated environment.
- `DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost`. Server binds E5 loopback only. `SECURE_SSL_REDIRECT=0` only inside this SSH-protected local preview.
- `DJANGO_EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend`: **real emails are not delivered**. When `NOSALE_LOCAL_EMAIL_PREVIEW=1`, only a browser session that requested the challenge from loopback can see its test code. This feature is guarded by `DEBUG`, loopback Host, console backend, disabled bank acquiring and disabled WB writes. Real mail deliverability requires a separate staging test using configured SMTP/relay and a real inbox; never treat preview mode as proof of delivery.

## Deploy a tested GitHub commit

```sh
repo=/srv/mpnosale-ui-preview/source
git -C "$repo" fetch origin feature/nosale-welcome-email-first
git -C "$repo" checkout --detach <CI-verified-commit-SHA>
cd "$repo/saas"
docker compose --env-file .preview.env -f deploy/ui-preview.compose.yml up -d --build
docker compose --env-file .preview.env -f deploy/ui-preview.compose.yml ps
docker exec mpnosale-ui-preview-web-1 python manage.py check
```

Never edit files on E5. Do not switch production branches or copy production accounts.

## Windows tunnel (scheduled task)

The scheduler action uses:

```powershell
& "C:\Program Files\Git\usr\bin\ssh.exe" -N -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -i "$([Environment]::GetFolderPath('UserProfile'))\.ssh\nosale-preview-ed25519" -L 127.0.0.1:8926:127.0.0.1:8894 nosale-preview-tunnel@infra.svdev.pro
```

Check the state with `Get-ScheduledTask -TaskName 'NoSale UI tunnel 8926'`. Avoid starting a second SSH tunnel, and **do not run `python manage.py runserver` locally**.

## Acceptance

- CI and PostgreSQL-backed test suite pass.
- Windows listener on port `8926` is `ssh.exe` and the task is running.
- Browser `/login/` and `/static/welcome.css` both return 200.
- E5 `docker exec mpnosale-ui-preview-web-1 python manage.py shell -c 'from django.db import connection; print(connection.vendor)'` prints `postgresql`.
- No code outside GitHub; after any UI change, push to GitHub, run CI, deploy a fixed SHA on E5, verify on the same localhost address.
