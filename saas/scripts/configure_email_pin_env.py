#!/usr/bin/env python3
"""Safely configure isolated MP NoSale SaaS email/PIN runtime without echoing secrets."""
import argparse
import os
from pathlib import Path
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--token-file", required=True, type=Path)
    args = parser.parse_args()
    path = args.env_file.resolve()
    token = args.token_file.read_text(encoding="utf-8").strip()
    if len(token) < 48 or any(c in token for c in "\r\n"):
        raise SystemExit("Relay token file invalid")

    updates = {
        "DJANGO_EMAIL_BACKEND": "portal.email_backend.RelayEmailBackend",
        "REQUIRE_EMAIL_VERIFICATION": "1",
        "SAAS_PIN_AUTH_ENABLED": "1",
        "MAIL_RELAY_URL": "https://mail.beclients.ru/svdev-relay.php",
        "MAIL_RELAY_TOKEN": token,
        "SMTP_FROM": "MP NoSale <noreply@svdev.pro>",
    }
    current_lines = path.read_text(encoding="utf-8").splitlines()
    seen = set()
    result = []
    for line in current_lines:
        key = line.split("=", 1)[0]
        if key not in updates:
            result.append(line)
        elif key not in seen:
            result.append(key + "=" + updates[key])
            seen.add(key)
    result.extend(k + "=" + v for k, v in updates.items() if k not in seen)
    if not path.is_file():
        raise SystemExit("SaaS env file missing")
    temp_fd, temp_name = tempfile.mkstemp(prefix=".otp-env.", dir=str(path.parent))
    try:
        os.fchmod(temp_fd, 0o600)
        with os.fdopen(temp_fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(result) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    print("MPNO_SAAS_EMAIL_PIN_ENV_CONFIGURED")


if __name__ == "__main__":
    main()
