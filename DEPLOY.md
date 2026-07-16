# Deployment Notes

`Marketplace Price Guard` can run in two modes.

## Local mode

Use `run.bat` on Windows. The service is available only on the same computer:

```text
http://127.0.0.1:8001
```

This mode is good for a first delivery, demo, and local seller workflow.

## Server mode

For 24/7 work:

1. Rent a VPS.
2. Install Python 3.11+.
3. Copy the project to the server.
4. Create `.env` from `.env.example`.
5. Set `APP_MODE=LIVE` only after checking the token and rules.
6. Run the app as a persistent service.
7. Put Nginx or Caddy in front of it.
8. Enable HTTPS.

Do not publish `.env`, `data/app.db`, real product exports, or client tokens.
