# NoSale production alerting (nosale.pro, current VPS)

- Runs on the **current production VPS**, every five minutes, independently of app web workers. Host checkout: `/opt/nosale/source/saas`; public health: `https://nosale.pro/health/`.
- Verifies healthy Docker status for DB, web, MCP, Guard, Notifications and Reconciliation; public `/health/`; daily offsite timer/service; freshness and SHA-256 of the latest local offsite archive.
- Uses existing production HTTPS mail relay and owner recipient from the already protected `saas/.env`. No new API keys, bot credentials or webhook endpoints.
- Sends **one owner email per incident** (only on healthy→unhealthy transition), no periodic reminders while the incident persists, and one recovery email when all checks pass. This prevents the old E5 6-hour reminder spam. Persists only issue strings and last notification time in `/var/lib/nosale-ops-alert/state.json` (root-only).
- Does not inspect seller records, start W​​B jobs, enable AI, or charge customers. A failed relay results in a failed systemd service and retry at next scheduled check, with sanitized journald output.

Install:
```
cd /opt/nosale/source/saas\ninstall -m 644 deploy/nosale-ops-alert.service /etc/systemd/system/
install -m 644 deploy/nosale-ops-alert.timer /etc/systemd/system/
systemctl daemon-reload
python3 deploy/nosale-ops-alert.py --check-only
## Do NOT run --test-email by default: it sends a real message to the owner.\n## Only request a single labeled test email with separate owner approval.
systemctl start nosale-ops-alert.service
systemctl enable --now nosale-ops-alert.timer
systemctl list-timers nosale-ops-alert.timer
```

Acceptance: `NOSALE_ISSUE_COUNT=0`, first scheduled `NOSALE_HEALTH_OK` without unsolicited mail, observed alerts on simulated health failure with stubbed external IO (unit tests), and correct **no-repeat** deduplication/recovery. Live owner mail delivery is not claimed until a separately approved test or real incident. Do **not** disable running containers to test in production.

This is a monitoring control, not automatic remediation. Docker `unhealthy` does not automatically restart containers. Existing offsite backup timer remains independent.

## Migration note (11.11.2026)

The former `infra.svdev.pro` E5 monitoring timer is disabled and must **not** be
re-enabled to monitor the retired E5 containers. The current VPS carries the
encrypted offsite timer and archives at `/opt/nosale/secure-backups/offsite`;
this monitor must be installed on that same host using the documented unit.
Run `--check-only` before enabling the timer. The first healthy scheduled
check should persist no incident and send **zero emails**. Avoid a live
failure injection: test failure detection and mail deduplication with
`test_nosale_ops_alert.py` mocks. Do not confuse monitoring readiness with
formal release approval in issue #12.
