# NoSale UI preview: correct development architecture

This is a **dedicated E5 test deployment**, not a local Django instance and not production.

- Source of truth: `Anderhaos/Marketplace-Price-Guard`, feature branch / immutable GitHub commit, draft PR.
- E5: `/srv/mpnosale-ui-preview/source/saas`, Compose `deploy/ui-preview.compose.yml`, `mpnosale-ui-preview` project, **own PostgreSQL 16 volume**, independent Django keys and env.
- E5 service listens **only** on `127.0.0.1:8894`. Public Nginx / prod `127.0.0.1:8892` and legacy `127.0.0.1:8891` remain untouched.
- Windows `Main`: reserved `127.0.0.1:8926` is an **SSH local tunnel only** to E5 `127.0.0.1:8894`; no locally running Django, SQLite, PostgreSQL or WB integration.
- Key-based port forwarding uses a dedicated unprivileged SSH tunnel identity restricted to permitted port; never copy production secrets or live customer data into preview.
- In preview `SAAS_WB_NETWORK_ENABLED=0`, `SAAS_WB_TOKEN_VALIDATION_ENABLED=0`, `NOSALE_TOCHKA_ENABLED=0`, `SAAS_ACCESS_ENFORCED=1`, `SAAS_PIN_AUTH_ENABLED=1`.
- For local-only mailbox tests, an isolated console email backend may be used; **this is NOT successful SMTP deliverability testing**. Codes must never be written to Git.
- Production migrations, real stores, live merchant credentials and real prices are not part of this preview.

## Server deployment from Git

```sh
cd /srv/mpnosale-ui-preview/source
git fetch origin feature/nosale-welcome-email-first
git checkout --detach <tested-immutable-sha>
cd saas
docker compose --env-file .preview.env -f deploy/ui-preview.compose.yml up -d --build
docker compose --env-file .preview.env -f deploy/ui-preview.compose.yml ps
```

## Windows UI tunnel

```powershell
ssh -N -T -i "$HOME\.ssh\nosale-preview-ed25519" -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:8926:127.0.0.1:8894 nosale-preview-tunnel@infra.svdev.pro
```

Then open `http://127.0.0.1:8926/login/`. Do not use a Django runserver on Windows for this project.

## Git release invariant

All source and Compose files are committed and pass CI before E5 deployment. The preview checkout is immutable and changed by fetching the next tested Git SHA, not by server-side edits.
