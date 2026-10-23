# NoSale — зарезервированный локальный предпросмотр интерфейса

## Device and port
- Windows workstation `Main`, loopback `http://127.0.0.1:8926/login/`.
- Port **8926 is reserved for the NoSale SaaS UI preview**. Do not assign it to Orbit, MiniBook, BeClients, Fakel or other projects.
- Clean GitHub worktree: `D:\agent-work\nosale-ui-preview`, based on `feature/nosale-welcome-email-first`.
- Isolated Python 3.12 venv: `D:\agent-work\.venvs\nosale-ui-8926`.
- PID and logs: `D:\agent-work\.run\nosale-8926.pid`, `nosale-8926.err.log` and `nosale-8926.out.log`.
- Test email uses console backend, never delivers messages to inboxes. Start Django unbuffered (`PYTHONUNBUFFERED=1`, `python -u manage.py runserver 127.0.0.1:8926 --noreload`) so current verification messages appear in `D:\agent-work\.run\nosale-8926-test-mail.out.log`. Codes last 10 minutes. Do not log or screenshot live customer codes, and never enable this console backend for production.

## Safety boundaries
This is a **local development preview**, not E5 staging and not the legacy real seller service. Use `DJANGO_DEBUG=1`, `SAAS_TEST_SQLITE=1`, `SAAS_PIN_AUTH_ENABLED=1`, `SAAS_WB_NETWORK_ENABLED=0`, `SAAS_WB_TOKEN_VALIDATION_ENABLED=0`, `SECURE_SSL_REDIRECT=0`, `DJANGO_EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend` and `REQUIRE_EMAIL_VERIFICATION=1`.

Never copy seller WB tokens, production PostgreSQL credentials or Tochka JWT keys into this preview. Do not reuse the local SQLite database for production.

## Workflow
1. Author code and docs directly in GitHub on a dedicated feature branch.
2. Verify GitHub Actions and local Django tests.
3. Fetch the reviewed GitHub commit into this clean local preview worktree; do not edit code here.
4. Restart the local preview if source changes; validate `/login/` and `/static/welcome.css` respond 200.
5. After visual approval, keep PR in draft until the full release checklist is completed.
