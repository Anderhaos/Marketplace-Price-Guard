#!/usr/bin/env python3
"""Public HTTPS E2E acceptance, including a real REG.RU email OTP.

For operator-only use on E5. Sends one email to owner@svdev.pro and creates
one disposable SaaS test user; ALWAYS remove that user after acceptance.
No OTP/PIN printed; no real marketplace API requests.
"""
import argparse
import email
from email.header import decode_header, make_header
from http.cookiejar import CookieJar
from html.parser import HTMLParser
from pathlib import Path
import re
import secrets
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import (
    HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, Request, build_opener,
)


BASE = "https://mpnosale-saas.svdev.pro"
TEST_EMAIL = "owner@svdev.pro"
MAILDIR = "/var/www/u3264726/data/email/svdev.pro/owner/.maildir"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CsrfFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            data = dict(attrs)
            if data.get("name") == "csrfmiddlewaretoken":
                self.token = data.get("value")


def request(opener, path, payload=None, cookiejar=None):
    headers = {}
    if payload is not None:
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": BASE,
            "Referer": BASE + path,
        }
    req = Request(BASE + path, data=urlencode(payload).encode() if payload is not None else None,
                  headers=headers, method="POST" if payload is not None else "GET")
    try:
        response = opener.open(req, timeout=14)
    except HTTPError as exc:
        response = exc
    try:
        body = response.read().decode("utf-8", errors="replace")
        return response.code, response.headers.get("Location", ""), body
    finally:
        response.close()


def get_form(opener, path):
    status, loc, body = request(opener, path)
    if status != 200:
        raise RuntimeError(f"Unexpected GET {path}: HTTP {status} location {loc[:100]}")
    parser = CsrfFormParser()
    parser.feed(body)
    if not parser.token:
        raise RuntimeError(f"No CSRF input on {path}")
    return parser.token


def post_redirect(opener, path, data, expected):
    csrf = get_form(opener, path)
    data["csrfmiddlewaretoken"] = csrf
    status, target, body = request(opener, path, data)
    if status != 302 or target != expected:
        raise RuntimeError(f"Unexpected POST {path}: HTTP {status} location {target[:100]} body {body[:90]}")
    return True


def fetch_mail_code(key, after_epoch):
    # Script running on REG.RU prints only the code to our private process pipe.
    remote_script = r"""
import email, pathlib, re, time
from email.header import decode_header, make_header
root=pathlib.Path('/var/www/u3264726/data/email/svdev.pro/owner/.maildir')
files=sorted((p for d in ('new','cur') for p in (root/d).iterdir() if p.is_file()),
             key=lambda p: p.stat().st_mtime, reverse=True)
for path in files[:18]:
    if path.stat().st_mtime < AFTER_TS:
        continue
    with path.open('rb') as f: msg=email.message_from_binary_file(f)
    subj=str(make_header(decode_header(msg.get('Subject',''))))
    if subj != 'Код подтверждения MP NoSale':
        continue
    body=msg.get_payload(decode=True)
    if body is None: continue
    code=re.search(r'Код:\s*(\d{6})',body.decode('utf-8',errors='replace'))
    if code:
        print(code.group(1))
        break
"""
    remote_script = remote_script.replace("AFTER_TS", str(int(after_epoch)-2))
    cmd = ["ssh", "-i", str(key), "-o", "BatchMode=yes",
           "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=6",
           "u3264726@server257.hosting.reg.ru", "python3", "-c", remote_script]
    for _ in range(10):
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=12, check=False)
        if result.returncode != 0:
            raise RuntimeError("Could not read the owned REG.RU test mailbox")
        code = result.stdout.strip()
        if re.fullmatch(r"\d{6}", code):
            return code
        time.sleep(2)
    raise RuntimeError("Expected real OTP email was not found")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh-key", required=True, type=Path)
    args = parser.parse_args()

    jar = CookieJar()
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(jar), NoRedirect())
    before = time.time()
    post_redirect(opener, "/register/", {"email": TEST_EMAIL}, "/auth/code/")
    print("PUBLIC_REGISTER_TO_OTP_PASS")
    otp = fetch_mail_code(args.ssh_key, before)
    post_redirect(opener, "/auth/code/", {"code": otp}, "/auth/pin/")
    print("REAL_EMAIL_OTP_VERIFIED_PASS")
    pin = "".join(str(secrets.randbelow(10)) for _ in range(6))
    if len(set(pin)) == 1:
        pin = "876241"
    post_redirect(opener, "/auth/pin/", {"pin": pin, "confirm_pin": pin}, "/")
    print("PIN_ENROLLMENT_PASS")
    status, _, html = request(opener, "/")
    if status != 200 or "Магазин" not in html:
        raise RuntimeError("Dashboard did not open after registration")
    print("AUTHENTICATED_DASHBOARD_PASS")
    csrf = get_form(opener, "/")
    status, target, _ = request(opener, "/logout/", {"csrfmiddlewaretoken": csrf})
    if status != 302 or target != "/login/":
        raise RuntimeError(f"Logout failed with HTTP {status} target {target}")
    post_redirect(opener, "/login/", {"email": TEST_EMAIL, "pin": pin}, "/")
    print("EMAIL_PIN_LOGIN_PASS")
    print("PUBLIC_MP_NO_SALE_OTP_E2E_PASS")


if __name__ == "__main__":
    main()
