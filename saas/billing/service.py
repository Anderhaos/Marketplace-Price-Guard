"""NoSale one-time subscription payments, using MiniBook's AccessGrant ledger.

Access is granted ONLY from verified, matching bank events. Browser redirects
can never activate a payment. Repeat events and concurrent requests are safe.
"""
import calendar
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .models import AccessGrant, SubscriptionPayment
from .tochka import (
    AcquiringUnavailable, InvalidWebhook, approved_price_kopeks,
    configured, create_tochka_link, normalize_bank_event, get_tochka_operation,
    validate_payment_url,
)


def _add_months(date, months):
    total = date.year * 12 + date.month - 1 + months
    year, month = divmod(total, 12)
    month += 1
    return date.replace(year=year, month=month, day=min(date.day, calendar.monthrange(year, month)[1]))


def create_checkout(user_id):
    if not configured():
        raise AcquiringUnavailable("Оплата пока не подключена")
    payment_error = None
    with transaction.atomic():
        # Serialized per seller: repeated clicks reuse the same unexpired link.
        user = get_user_model().objects.select_for_update().get(pk=user_id)
        amount = approved_price_kopeks()
        now = timezone.now()
        existing = SubscriptionPayment.objects.filter(
            user=user, provider="tochka", status="pending",
            amount_kopeks=amount, period_months=1,
            link_expires_at__gt=now,
        ).exclude(payment_url="").order_by("-created_at").first()
        if existing:
            return existing
        payment = SubscriptionPayment.objects.create(
            user=user, provider="tochka", amount_kopeks=amount,
            currency="RUB", period_months=1,
            link_expires_at=now + timedelta(minutes=30),
        )
        # Keep a durable payment row even if Tochka times out. Never fabricate a
        # successful payment from a network error, local redirect, or test token.
        try:
            url, operation_id = create_tochka_link(payment)
        except AcquiringUnavailable as error:
            payment.status = "failed"
            payment.save(update_fields=["status"])
            payment_error = error
        else:
            payment.payment_url = url
            payment.operation_id = operation_id
            payment.save(update_fields=["payment_url", "operation_id"])
    if payment_error:
        raise payment_error
    return payment


def _money_to_kopeks(value):
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidWebhook("Invalid amount") from exc
    if not decimal.is_finite() or decimal < 0 or decimal.as_tuple().exponent < -2:
        raise InvalidWebhook("Invalid amount")
    return int(decimal * 100)


@transaction.atomic
def apply_verified_event(payload):
    event = normalize_bank_event(payload)
    operation = event["operation_id"]
    link_id = event["payment_link_id"]
    if not isinstance(operation, str) or not operation or len(operation) > 128:
        raise InvalidWebhook("Missing operation identifier")
    # link id is a NoSale DB pk, unique only inside this application.
    if not isinstance(link_id, str) or not link_id.isdecimal():
        raise InvalidWebhook("Missing independent payment link ID")
    candidate = SubscriptionPayment.objects.filter(pk=int(link_id), provider="tochka").first()
    if candidate is None:
        raise InvalidWebhook("Unknown payment")
    # Serialize all payments for the same account so concurrent bank events
    # cannot create overlapping monthly grants.
    get_user_model().objects.select_for_update().get(pk=candidate.user_id)
    payment = SubscriptionPayment.objects.select_for_update().get(pk=candidate.pk)
    if payment is None:
        raise InvalidWebhook("Unknown payment")
    if payment.operation_id and payment.operation_id != operation:
        raise InvalidWebhook("Wrong operation ID")
    if (str(event["merchant_id"]) != settings.TOCHKA_MERCHANT_ID
            or str(event["customer_code"]) != settings.TOCHKA_CUSTOMER_CODE):
        raise InvalidWebhook("Foreign merchant")
    if event["amount"] is None or _money_to_kopeks(event["amount"]) != payment.amount_kopeks:
        raise InvalidWebhook("Amount mismatch")
    status = event["status"]
    if status == "REFUNDED":
        if payment.status == "refunded":
            return "duplicate_refund"
        # A verified refund can arrive before an approval; it must be terminal
        # so a delayed APPROVED event cannot grant access after money returned.
        payment.status = "refunded"
        payment.save(update_fields=["status"])
        AccessGrant.objects.filter(
            user_id=payment.user_id, source="payment",
            source_ref=f"nosale:tochka:{payment.pk}",
        ).update(revoked_at=timezone.now())
        return "refunded"
    # Pre-authorization is not money captured; do not grant access.
    if status != "APPROVED":
        return "ignored"
    if payment.status == "paid":
        return "duplicate"
    # A bank may have accepted the link but its HTTP response was lost.
    # A matching signed bank APPROVED event is stronger evidence than the
    # local failed link request, and must recover the paid entitlement.
    if payment.status not in ("pending", "failed"):
        return "ignored"
    if not payment.operation_id:
        payment.operation_id = operation
    now = timezone.now()
    previous = AccessGrant.objects.filter(
        user_id=payment.user_id, revoked_at__isnull=True,
        ends_at__gt=now,
    ).order_by("-ends_at").first()
    start = max(now, previous.ends_at) if previous else now
    AccessGrant.objects.create(
        user_id=payment.user_id,
        source="payment",
        source_ref=f"nosale:tochka:{payment.pk}",
        starts_at=start,
        ends_at=_add_months(start, payment.period_months),
    )
    payment.status = "paid"
    payment.paid_at = now
    payment.save(update_fields=["operation_id", "status", "paid_at"])
    return "activated"

def reconcile_payment(payment):
    """Fail-closed status reconciliation using Tochka's authenticated GET.

    All monetary/merchant/order checks are reused from apply_verified_event.
    """
    if not payment.operation_id:
        return "missing_bank_operation"
    operation = get_tochka_operation(payment.operation_id)
    if operation.get("status") == "REFUNDED_PARTIALLY":
        # Partial refunds need a separately approved entitlement policy.
        return "partial_refund_manual_review"
    event = {"data": {
        "webhookType": "acquiringInternetPayment",
        "operationId": operation.get("operationId"),
        "paymentLinkId": operation.get("paymentLinkId"),
        "merchantId": operation.get("merchantId"),
        "customerCode": operation.get("customerCode"),
        "status": operation.get("status"),
        "amount": operation.get("amount"),
    }}
    return apply_verified_event(event)
