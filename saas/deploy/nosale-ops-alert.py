#!/usr/bin/env python3
"""Host-side NoSale liveness / backup monitor with owner email on transitions.

Runs with systemd as root and uses the same restricted mail relay as NoSale OTP.
Never reads seller records, changes app settings, or invokes WB/AI/Tochka APIs.
"""
import argparse
import json
import os
from pathlib import Path
import re
from email.utils import parseaddr
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler

APP_ENV = Path("/srv/mpnosale-saas/source/saas/.env")
STATE = Path("/var/lib/nosale-ops-alert/state.json")
BACKUPS = Path("/srv/mpnosale-saas/secure-backups/offsite")
CONTAINERS = (
    "mpnosale-saas-db-1",
    "mpnosale-saas-web-1",
    "mpnosale-saas-mcp-1",
    "mpnosale-saas-worker-1",
    "mpnosale-saas-notifications-1",
    "mpnosale-saas-reconciliation-1",
)
BACKUP_MAX_AGE_SECONDS = 40 * 3600
REMINDER_INTERVAL_SECONDS = 6 * 3600
HEALTH_URL = "https://nosale.svdev.pro/health/"


def command(*args, timeout=12):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout).stdout.strip()


def collect_issues(now=None):
    now = time.time() if now is None else now
    issues = []
    for container in CONTAINERS:
        try:
            payload = json.loads(command("docker", "inspect", "--format", "{{json .State}}", container))
            if payload.get("Status") != "running":
                issues.append(f"{container}: container not running")
            elif (payload.get("Health") or {}).get("Status") != "healthy":
                issues.append(f"{container}: Docker health is not healthy")
        except (OSError, ValueError, subprocess.SubprocessError):
            issues.append(f"{container}: Docker inspection failed")

    try:
        if command("systemctl", "is-active", "nosale-backup-offsite.timer") != "active":
            issues.append("daily offsite backup timer is inactive")
        if command("systemctl", "show", "nosale-backup-offsite.service", "-p", "Result", "--value") != "success":
            issues.append("most recent offsite backup service failed")
    except (OSError, subprocess.SubprocessError):
        issues.append("offsite backup systemd status unavailable")

    archives = sorted(BACKUPS.glob("20*T*Z/SHA256SUMS"))
    if not archives:
        issues.append("no completed local offsite backup exists")
    else:
        newest = max(archives, key=lambda path: path.stat().st_mtime)
        age = now - newest.stat().st_mtime
        if age < -300 or age > BACKUP_MAX_AGE_SECONDS:
            issues.append("latest completed offsite backup is missing or stale")
        else:
            try:
                subprocess.run(("sha256sum", "-c", "SHA256SUMS"), cwd=newest.parent,
                               check=True, capture_output=True, timeout=15)
            except (OSError, subprocess.SubprocessError):
                issues.append("latest local offsite archive checksum failed")

    try:
        status = command("curl", "--silent", "--show-error", "--max-redirs", "0", "--max-time", "8",
                         "--output", "/dev/null", "--write-out", "%{http_code}", HEALTH_URL)
        if status != "200":
            issues.append("public NoSale health endpoint returned non-200")
    except (OSError, subprocess.SubprocessError):
        issues.append("public NoSale health endpoint unreachable")
    return sorted(set(issues))


def read_env(path=APP_ENV):
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        env[key] = value
    return env


def send_owner_alert(subject, body, env):
    url = env.get("MAIL_RELAY_URL", "")
    token = env.get("MAIL_RELAY_TOKEN", "")
    owner = env.get("NOSALE_OWNER_EMAIL", "")
    sender = env.get("SMTP_FROM", "")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "mail.beclients.ru"
            or parsed.path != "/svdev-relay.php" or parsed.query or parsed.fragment
            or parsed.port not in (None, 443) or parsed.username or parsed.password):
        raise RuntimeError("mail relay URL is not the approved NoSale endpoint")
    if len(token) < 32 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", owner):
        raise RuntimeError("mail relay credentials/owner recipient are unconfigured")
    if not parseaddr(sender)[1].lower().endswith("@svdev.pro"):
        raise RuntimeError("mail sender must match the configured domain")

    payload = json.dumps({
        "kind": "codes", "to": owner, "subject": subject,
        "text": body, "html": "",
    }, ensure_ascii=False).encode("utf-8")
    req = Request(url, method="POST", data=payload,
                  headers={"Content-Type": "application/json", "x-svdev-relay-token": token})
    with build_opener(ProxyHandler({})).open(req, timeout=12) as response:
        if response.status not in (200, 202):
            raise RuntimeError("mail relay rejected alert")


def load_state(path=STATE):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data.get("issues"), list) and isinstance(data.get("last_sent"), (int, float)):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {"issues": [], "last_sent": 0}


def monitor(issues, now, old):
    previous = sorted(set(old.get("issues", [])))
    issues = sorted(set(issues))
    if issues:
        if issues != previous or now - old.get("last_sent", 0) >= REMINDER_INTERVAL_SECONDS:
            return ("[NoSale] Авария сервисов", "NoSale E5 requires attention:\n\n"
                    + "\n".join("- " + item for item in issues)
                    + "\n\nCheck: docker ps; systemctl status nosale-backup-offsite.service")
    elif previous:
        return "[NoSale] Сервисы восстановлены", "NoSale E5 health and backup checks are back to normal."
    return None


def save_state(issues, now, path=STATE):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"issues": issues, "last_sent": now}), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true", help="Print issue count; no email/state")
    parser.add_argument("--test-email", action="store_true", help="Send a labeled test email without changing monitoring state")
    args = parser.parse_args()
    if args.test_email:
        send_owner_alert("[NoSale] Проверка аварийных уведомлений",
                         "Тестовое сообщение NoSale E5. Сбоев не обнаружено; это проверка канала оповещения.",
                         read_env())
        print("NOSALE_TEST_EMAIL_SENT")
        return 0
    issues = collect_issues()
    if args.check_only:
        print(f"NOSALE_ISSUE_COUNT={len(issues)}")
        for issue in issues:
            print(issue)
        return int(bool(issues))
    now = time.time()
    old = load_state()
    notification = monitor(issues, now, old)
    if notification:
        # Do not record the new alert state until the mail gateway ACKs it.
        send_owner_alert(*notification, read_env())
        save_state(issues, now)
        print("NOSALE_ALERT_SENT")
    else:
        print("NOSALE_HEALTH_OK" if not issues else "NOSALE_ALERT_DEDUPED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # Never print credentials, HTTP response bodies, or private env details.
        print(f"NOSALE_MONITOR_FAILED={type(exc).__name__}", file=sys.stderr)
        sys.exit(2)
