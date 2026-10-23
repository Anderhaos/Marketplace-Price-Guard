"""Tochka acquiring adapter, following MiniBook's payment-link + signed-JWT model.

Strictly no unsignalled sandbox or simulated payment activation in production.
All merchant values are isolated to NoSale's environment.
"""
import base64
import binascii
import json
import time
from decimal import Decimal
from urllib.parse import urlparse, quote
from urllib.request import Request, ProxyHandler, build_opener

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings


class AcquiringUnavailable(Exception):
    pass


class InvalidWebhook(Exception):
    pass


def configured():
    return bool(settings.TOCHKA_BILLING_ENABLED)


def rubles_to_kopeks(value):
    amount = Decimal(str(value))
    if amount <= 0 or amount.as_tuple().exponent < -2:
        raise ValueError("Invalid fixed RUB price")
    return int(amount * 100)


def approved_price_kopeks():
    return rubles_to_kopeks(settings.NOSALE_MONTHLY_PRICE_RUB)


def _from_data(response):
    return response.get("Data", response) if isinstance(response, dict) else {}


def validate_payment_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise AcquiringUnavailable("Invalid payment URL")
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise AcquiringUnavailable("Invalid acquiring payment port") from exc
    # An attacker must never be able to redirect the seller to an arbitrary host.
    if (parsed.scheme != "https" or parsed.username or parsed.password or port
            or not parsed.hostname or
            (parsed.hostname != "merch.tochka.com" and not parsed.hostname.endswith(".tochka.com"))
            or parsed.fragment):
        raise AcquiringUnavailable("Unexpected acquiring payment host")
    return value


def create_tochka_link(payment):
    if not configured():
        raise AcquiringUnavailable("Acquiring has not been activated")
    amount = Decimal(payment.amount_kopeks) / 100
    base = settings.NOSALE_PAYMENT_RETURN_BASE
    body = {"Data": {
        "amount": float(amount),
        "customerCode": settings.TOCHKA_CUSTOMER_CODE,
        "merchantId": settings.TOCHKA_MERCHANT_ID,
        "purpose": f"NoSale: доступ на {payment.period_months} месяц",
        "paymentMode": ["card", "sbp"],
        "paymentLinkId": str(payment.pk),
        "redirectUrl": base + "/billing/return/?payment=" + str(payment.pk),
        "failRedirectUrl": base + "/billing/return/?payment=" + str(payment.pk),
        "webhookUrl": base + "/billing/tochka/webhook/",
        "ttl": 30,
    }}
    req = Request(
        settings.TOCHKA_API_BASE + "/acquiring/v1.0/payments",
        method="POST",
        headers={"Authorization": "Bearer " + settings.TOCHKA_JWT_TOKEN, "Content-Type": "application/json"},
        data=json.dumps(body, separators=(",", ":")).encode(),
    )
    try:
        with build_opener(ProxyHandler({})).open(req, timeout=12) as response:
            if response.status != 200:
                raise AcquiringUnavailable("Payment link declined by Tochka")
            data = json.loads(response.read(65536))
    except (OSError, ValueError) as exc:
        raise AcquiringUnavailable("Tochka acquiring temporarily unavailable") from exc
    obj = _from_data(data)
    if not isinstance(obj, dict):
        raise AcquiringUnavailable("Invalid acquiring reply")
    url = obj.get("paymentLink") or obj.get("paymentUrl") or obj.get("url")
    operation = obj.get("operationId")
    if not isinstance(operation, str) or not operation or len(operation) > 128:
        raise AcquiringUnavailable("Missing operation identifier")
    return validate_payment_url(url), operation


def get_tochka_operation(operation_id):
    """Authenticated readback from Tochka when a signed webhook is lost.

    Official response: Data.Operation[0] per Get Payment Operation Info.
    This is an independent TLS-validated server-to-server read, not an
    untrusted browser callback or unsigned incoming webhook.
    """
    if not configured():
        raise AcquiringUnavailable("Acquiring disabled")
    if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 128:
        raise AcquiringUnavailable("Invalid bank operation identifier")
    # Whitelist URL-safe identifiers; prevent injection into URL paths.
    if not all(c.isascii() and (c.isalnum() or c == "-") for c in operation_id):
        raise AcquiringUnavailable("Invalid bank operation identifier")
    request = Request(
        settings.TOCHKA_API_BASE + "/acquiring/v1.0/payments/" + quote(operation_id, safe=""),
        headers={"Authorization": "Bearer " + settings.TOCHKA_JWT_TOKEN, "Accept": "application/json"},
        method="GET",
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=12) as response:
            if response.status != 200:
                raise AcquiringUnavailable("Bank status lookup failed")
            result = json.loads(response.read(65536))
    except (OSError, ValueError) as exc:
        raise AcquiringUnavailable("Bank status lookup temporarily unavailable") from exc
    operations = result.get("Data", {}).get("Operation") if isinstance(result, dict) else None
    if not isinstance(operations, list) or len(operations) != 1 or not isinstance(operations[0], dict):
        raise AcquiringUnavailable("Invalid bank operation response")
    operation = operations[0]
    if operation.get("operationId") != operation_id:
        raise AcquiringUnavailable("Bank returned another operation")
    return operation


def _decoded(part):
    part += "=" * (-len(part) % 4)
    try:
        return base64.urlsafe_b64decode(part.encode())
    except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
        raise InvalidWebhook("Invalid JWT encoding") from exc


def verify_tochka_webhook(raw):
    """Verify RS256 signature against operator-configured public Tochka JWK.

    Importantly, this function does not contact third-party URLs or trust
    algorithm/kid/remote JWKS hints from the untrusted JWT header.
    """
    if not configured() or not isinstance(raw, bytes) or len(raw) > 16000:
        raise InvalidWebhook("Webhook disabled or oversized")
    try:
        encoded = raw.decode("ascii").strip()
        head64, body64, sig64 = encoded.split(".")
        head = json.loads(_decoded(head64))
        payload = json.loads(_decoded(body64))
        jwk = json.loads(settings.TOCHKA_WEBHOOK_JWK_JSON)
        if head.get("alg") != "RS256" or not isinstance(payload, dict) or jwk.get("kty") != "RSA":
            raise InvalidWebhook("Unsupported JWT")
        n = int.from_bytes(_decoded(jwk["n"]), "big")
        e = int.from_bytes(_decoded(jwk["e"]), "big")
        rsa.RSAPublicNumbers(e=e, n=n).public_key().verify(
            _decoded(sig64), (head64 + "." + body64).encode(),
            padding.PKCS1v15(), hashes.SHA256(),
        )
        # Tochka webhooks are verified by signature; timestamp claims are
        # validated if present (the bank's signed event can omit exp).
        now = int(time.time())
        if "iat" in payload and not (now - 7 * 86400 <= int(payload["iat"]) <= now + 300):
            raise InvalidWebhook("Invalid issued-at")
        if "exp" in payload and int(payload["exp"]) < now:
            raise InvalidWebhook("Expired webhook")
        return payload
    except (ValueError, TypeError, KeyError, UnicodeError, InvalidSignature) as exc:
        raise InvalidWebhook("Invalid bank signature or payload") from exc


def _field(data, *keys):
    for key in keys:
        if key in data:
            return data[key]
    return None


def normalize_bank_event(payload):
    inner = payload.get("data") or payload.get("Data")
    if not isinstance(inner, dict):
        inner = payload
    event_type = _field(inner, "eventType", "webhookType") or _field(payload, "eventType", "webhookType")
    if event_type != "acquiringInternetPayment":
        raise InvalidWebhook("Not an acquiring payment event")
    return {
        "operation_id": _field(inner, "operationId", "operation_id"),
        "payment_link_id": _field(inner, "paymentLinkId", "payment_link_id"),
        "status": _field(inner, "status"),
        "amount": _field(inner, "amount"),
        "merchant_id": _field(inner, "merchantId", "merchant_id"),
        "customer_code": _field(inner, "customerCode", "customer_code"),
    }
