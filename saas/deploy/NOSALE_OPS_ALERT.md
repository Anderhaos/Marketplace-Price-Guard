# NoSale production alerting (E5)

- Runs on the **host**, every five minutes, independently of app web workers.
- Verifies healthy Docker status for DB, web, MCP, Guard, Notifications and Reconciliation; public `/health/`; daily offsite timer/service; freshness and SHA-256 of the latest local offsite archive.
- Uses existing production HTTPS mail relay and owner recipient from the already protected `saas/.env`. No new API keys, bot credentials or webhook endpoints.
- Sends on problem transitions, repeats continuing outages at most every six hours, and sends a recovery notice. Persists only issue strings and last notification time in `/var/lib/nosale-ops-alert/state.json` (root-only).
- Does not inspect seller records, start W​​B jobs, enable AI, or charge customers. A failed relay results in a failed systemd service and retry at next scheduled check, with sanitized journald output.

Install:
```
install -m 644 deploy/nosale-ops-alert.service /etc/systemd/system/
install -m 644 deploy/nosale-ops-alert.timer /etc/systemd/system/
systemctl daemon-reload
python3 deploy/nosale-ops-alert.py --check-only
python3 deploy/nosale-ops-alert.py --test-email  # sends one explicitly labeled test email
systemctl start nosale-ops-alert.service
systemctl enable --now nosale-ops-alert.timer
systemctl list-timers nosale-ops-alert.timer
```

Acceptance: `NOSALE_ISSUE_COUNT=0`, one accepted test email, first scheduled `NOSALE_HEALTH_OK`, observed alerts on simulated health failure with stubbed external IO (unit tests), and correct deduplication/recovery. Do **not** disable running containers to test in production.

This is a monitoring control, not automatic remediation. Docker `unhealthy` does not automatically restart containers. Existing offsite backup timer remains independent.
