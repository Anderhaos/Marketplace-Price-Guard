"""Django email backend for the established BeClients-compatible HTTPS mail relay.

The relay chooses the verified From address from its own configuration. Neither
the client nor the Django message may override that identity.
"""
import json
import logging
import time
from email.utils import parseaddr
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, build_opener

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend


_log = logging.getLogger(__name__)


class RelayEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        url = settings.MAIL_RELAY_URL
        token = settings.MAIL_RELAY_TOKEN
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("MAIL_RELAY_TOKEN must be configured")
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != "mail.beclients.ru"
                or parsed.path != "/svdev-relay.php" or parsed.query or parsed.fragment
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise ValueError("MAIL_RELAY_URL must use the trusted SVDEV HTTPS relay")
        count = 0
        for message in email_messages:
            recipients = message.recipients()
            sender = parseaddr(message.from_email or "")[1].lower()
            expected_sender = parseaddr(settings.DEFAULT_FROM_EMAIL)[1].lower()
            if sender != expected_sender or not sender.endswith("@svdev.pro"):
                raise ValueError("Mail sender does not match the configured domain")
            if not recipients or len(recipients) > 10:
                raise ValueError("Invalid recipient count")
            if message.attachments or getattr(message, "extra_headers", {}):
                raise ValueError("Attachments and custom headers are not supported")
            html = ""
            for alternative in getattr(message, "alternatives", []):
                if alternative.mimetype == "text/html":
                    html = alternative.content
            body = {
                "kind": "codes",
                "to": ", ".join(recipients),
                "subject": message.subject,
                "text": message.body,
                "html": html,
            }
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req = Request(
                url, data=payload, method="POST",
                headers={"Content-Type": "application/json",
                         "x-svdev-relay-token": token},
            )
            started_at = time.monotonic()
            try:
                with build_opener(ProxyHandler({})).open(req, timeout=12) as response:
                    if response.status not in (200, 202):
                        raise OSError("Relay rejected email")
                    _log.info("mpno_mail_relay_accepted http_status=%d latency_ms=%d", response.status, round((time.monotonic()-started_at)*1000))
            except (HTTPError, URLError, TimeoutError, OSError):
                if self.fail_silently:
                    continue
                raise
            count += 1
        return count
