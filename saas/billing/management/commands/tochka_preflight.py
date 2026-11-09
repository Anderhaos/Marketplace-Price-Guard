"""Fail-closed NoSale-only Tochka preflight against the official bank API.

Source of truth: https://developers.tochka.com/docs/tochka-api/
No payment can be created by this command, and it never enables checkout.
"""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, ProxyHandler, build_opener

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

BANK_API = "https://enter.tochka.com/uapi"
PUBLIC_KEY_URL = "https://enter.tochka.com/doc/openapi/static/keys/public"
WEBHOOK_URL = "https://nosale.pro/billing/tochka/webhook/"
EVENT_NAME = "acquiringInternetPayment"


def retailer_for_nosale(data, merchant_id):
    """Select exactly the verified, registered NoSale trading point."""
    retailers = data.get("Data", {}).get("Retailer", [])
    if not isinstance(retailers, list):
        return None
    for retailer in retailers:
        if not isinstance(retailer, dict) or retailer.get("merchantId") != merchant_id:
            continue
        raw_url = str(retailer.get("url") or "").strip()
        if not raw_url:
            continue
        parsed = urlparse(raw_url if "://" in raw_url else "https://" + raw_url)
        if (parsed.hostname == "nosale.pro"
                and retailer.get("status") == "REG"
                and retailer.get("isActive") is True):
            return retailer
    return None


def webhook_matches(data):
    details = data.get("Data", {})
    return (
        isinstance(details, dict)
        and details.get("url") == WEBHOOK_URL
        and EVENT_NAME in details.get("webhooksList", [])
    )


def _request(path_or_url, token=None, *, method="GET", body=None, allow_404=False):
    """Strict TLS, no proxy override needed, no token or raw bank response in logs."""
    url = path_or_url if path_or_url.startswith("https://") else BANK_API + path_or_url
    if not (url == PUBLIC_KEY_URL or url.startswith(BANK_API + "/")):
        raise CommandError("Unexpected Tochka host")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    try:
        request = Request(url, method=method, headers=headers, data=data)
        with build_opener(ProxyHandler({})).open(request, timeout=12) as response:
            if response.status != 200:
                raise CommandError(f"Tochka {method} failed: HTTP {response.status}")
            result = json.loads(response.read(128_000))
    except HTTPError as exc:
        if exc.code == 404 and allow_404:
            return None
        raise CommandError(f"Tochka {method} failed: HTTP {exc.code}") from exc
    except (URLError, ValueError, OSError) as exc:
        raise CommandError(f"Tochka {method}: HTTPS/JSON failure ({type(exc).__name__})") from exc
    if not isinstance(result, dict):
        raise CommandError("Tochka returned an invalid JSON object")
    return result


class Command(BaseCommand):
    help = "Read-only Tochka readiness check, optionally register NoSale's own webhook"

    def add_arguments(self, parser):
        parser.add_argument("--online", action="store_true", help="Check real bank merchant, app webhook and public key")
        parser.add_argument(
            "--register-webhook", action="store_true",
            help="ONLY if absent, register NoSale's webhook on its dedicated client_id",
        )

    def handle(self, *args, **options):
        enabled = settings.TOCHKA_BILLING_ENABLED
        fiscal = settings.NOSALE_FISCALIZATION_CONFIRMED
        token = settings.TOCHKA_JWT_TOKEN
        customer = settings.TOCHKA_CUSTOMER_CODE
        merchant = settings.TOCHKA_MERCHANT_ID
        app = os.environ.get("NOSALE_TOCHKA_CLIENT_ID", "").strip()
        key = settings.TOCHKA_WEBHOOK_JWK_JSON
        self.stdout.write(
            f"NoSale local: checkout={'on' if enabled else 'off'}, "
            f"receipts_confirmed={'yes' if fiscal else 'no'}, "
            f"jwt={'set' if token else 'missing'}, "
            f"customer={'set' if customer else 'missing'}, "
            f"merchant={'set' if merchant else 'missing'}, "
            f"client_id={'set' if app else 'missing'}, "
            f"webhook_key={'set' if key else 'missing'}"
        )
        if options["register_webhook"] and not options["online"]:
            raise CommandError("Registering a webhook requires --online")
        if not options["online"]:
            return

        if not all((token, customer, merchant, app)):
            raise CommandError("NoSale JWT, customerCode, merchantId and client_id must be configured first")
        if settings.TOCHKA_API_BASE != BANK_API:
            raise CommandError("Bank API must use the official production URL")
        if (len(customer) != 9 or not customer.isascii() or not customer.isalnum()
                or len(merchant) != 15 or not merchant.isdecimal()
                or len(app) > 128 or not app.isascii() or not app.isalnum()):
            raise CommandError("Invalid Tochka identifier format")

        # Official read-only Get Retailers. Do not trust an ID copied from another app.
        response = _request("/acquiring/v1.0/retailers?" + urlencode({"customerCode": customer}), token)
        retailer = retailer_for_nosale(response, merchant)
        if not retailer:
            raise CommandError("No active REG trading point for nosale.pro matches the configured merchantId")
        payment_modes = retailer.get("paymentModes", [])
        if not isinstance(payment_modes, list) or not {"card", "sbp"}.issubset(payment_modes):
            raise CommandError("NoSale retailer is missing card or SBP payment capability")
        self.stdout.write("Bank merchant: REG, active, nosale.pro, cards+SBP confirmed")
        self.stdout.write(
            "Bank cashbox: " + ("reported" if retailer.get("cashbox") else "not reported (verify receipts in Tochka)")
        )

        public = _request(PUBLIC_KEY_URL)
        if not all((public.get("kty") == "RSA", isinstance(public.get("n"), str), public.get("e") == "AQAB")):
            raise CommandError("Official Tochka public webhook key is malformed")
        if key:
            try:
                local = json.loads(key)
            except ValueError as exc:
                raise CommandError("Configured NoSale webhook JWK is invalid JSON") from exc
            if local.get("n") != public["n"] or local.get("e") != public["e"]:
                raise CommandError("Configured webhook JWK does not match the official Tochka key")
            self.stdout.write("Webhook signature key: matches official Tochka key")
        else:
            self.stdout.write("Webhook signature key: missing locally (do not enable acquiring)")

        # A client_id supports exactly one webhook URL. Never overwrite a webhook
        # belonging to another application; user must issue a new NoSale JWT.
        endpoint = "/webhook/v1.0/" + quote(app, safe="")
        current = _request(endpoint, token, allow_404=True)
        if current is not None:
            if not webhook_matches(current):
                raise CommandError("Existing webhook belongs to another URL/event; not overwriting it")
            self.stdout.write("NoSale webhook: already registered on the correct URL")
            return
        self.stdout.write("NoSale webhook: not registered")
        if not options["register_webhook"]:
            self.stdout.write("Register via --online --register-webhook once the dedicated JWT is installed")
            return

        created = _request(
            endpoint, token, method="PUT",
            body={"webhooksList": [EVENT_NAME], "url": WEBHOOK_URL},
        )
        if not webhook_matches(created):
            raise CommandError("Tochka webhook response did not confirm NoSale URL/event")
        self.stdout.write("NoSale webhook: registered, acquiring remains unchanged")
