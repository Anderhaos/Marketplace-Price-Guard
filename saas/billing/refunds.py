"""Owner-confirmed full refunds via official Tochka acquiring API.

Never infer completion from HTTP 200 or the buyer's browser redirect.
Exactly one persisted attempt per payment prevents duplicate POST requests,
including after ambiguous timeouts. No automated retries.
"""
import json
import logging
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener

from django.conf import settings
from django.db import IntegrityError, transaction

from .models import PaymentRefundAttempt, SubscriptionPayment
from .service import reconcile_payment
from .tochka import AcquiringUnavailable, InvalidWebhook, get_tochka_operation

log = logging.getLogger(__name__)


class RefundUnavailable(Exception):
    """A full refund cannot safely be started for this payment."""


def _kopeks(value):
    try:
        money = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RefundUnavailable("Банк вернул неверную сумму.") from exc
    if not money.is_finite() or money <= 0 or money.as_tuple().exponent < -2:
        raise RefundUnavailable("Банк вернул неверную сумму.")
    return int(money * 100)


def _verify_bank_payment(payment, operation):
    """Independent bank readback must match ALL stored NoSale payment identities."""
    if (operation.get("operationId") != payment.operation_id
            or str(operation.get("merchantId")) != settings.TOCHKA_MERCHANT_ID
            or str(operation.get("customerCode")) != settings.TOCHKA_CUSTOMER_CODE
            or operation.get("paymentLinkId") not in (f"nosale-{payment.pk}", str(payment.pk))
            or _kopeks(operation.get("amount")) != payment.amount_kopeks):
        raise RefundUnavailable("Данные платежа в банке не совпадают с NoSale.")
    if operation.get("status") != "APPROVED":
        raise RefundUnavailable("Банк не разрешает возврат: платёж уже возвращён или не подтверждён.")
    orders = operation.get("Order", [])
    if not isinstance(orders, list) or any(
        not isinstance(entry, dict) or entry.get("type") == "refund" for entry in orders
    ):
        raise RefundUnavailable("Банк уже содержит запрос возврата или вернул неопределённые операции.")


def _send_full_refund(payment):
    """One POST to Tochka, with precisely the server-side paid amount in RUB."""
    if settings.TOCHKA_API_BASE != "https://enter.tochka.com/uapi":
        raise RefundUnavailable("Не настроен официальный адрес API Точки.")
    if not payment.operation_id or not all(
        character.isascii() and (character.isalnum() or character == "-")
        for character in payment.operation_id
    ):
        raise RefundUnavailable("Недопустимый идентификатор банковской операции.")
    amount = float(Decimal(payment.amount_kopeks) / Decimal(100))
    request = Request(
        settings.TOCHKA_API_BASE + "/acquiring/v1.0/payments/" + payment.operation_id + "/refund",
        method="POST",
        headers={
            "Authorization": "Bearer " + settings.TOCHKA_JWT_TOKEN,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        data=json.dumps({"Data": {"amount": amount}}, separators=(",", ":")).encode(),
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=12) as response:
            if response.status != 200:
                raise RefundUnavailable("Точка вернула неожиданный HTTP-статус.")
            result = json.loads(response.read(65536))
    except HTTPError as exc:
        # No automatic retry, even for 4xx: the bank could be racing a refund.
        log.warning("nosale_refund_bank_http payment=%s code=%s", payment.pk, exc.code)
        raise RefundUnavailable("Точка не подтвердила запрос. Проверьте статус в банке.") from exc
    except (URLError, OSError, ValueError) as exc:
        log.warning("nosale_refund_bank_uncertain payment=%s type=%s", payment.pk, type(exc).__name__)
        raise RefundUnavailable("Ответ банка не получен. Проверяйте статус, не повторяйте запрос.") from exc
    if not isinstance(result, dict) or not isinstance(result.get("Data"), dict):
        raise RefundUnavailable("Неоднозначный ответ Точки. Проверьте статус в банке.")
    return result["Data"]


def _update_attempt(attempt, *, status, bank_status="", order_id="", failure_code=""):
    attempt.status = status
    attempt.bank_status = bank_status
    if order_id:
        attempt.bank_refund_order_id = str(order_id)[:128]
    attempt.failure_code = failure_code
    attempt.save(update_fields=[
        "status", "bank_status", "bank_refund_order_id", "failure_code", "updated_at",
    ])
    return attempt


def start_full_refund(payment_id, actor):
    """Explicit owner action: preflight, unique durable audit row, one bank POST."""
    if not actor.is_active or not actor.is_superuser:
        raise RefundUnavailable("Возврат разрешён только владельцу NoSale.")
    if not settings.TOCHKA_BILLING_ENABLED:
        raise RefundUnavailable("Эквайринг отключён.")
    payment = SubscriptionPayment.objects.select_related("user").filter(pk=payment_id).first()
    if payment is None or payment.provider != "tochka" or payment.status != "paid":
        raise RefundUnavailable("Платёж не найден или уже не оплачен.")
    if not payment.operation_id or not 0 < payment.amount_kopeks <= 10_000_000:
        raise RefundUnavailable("Нет подтверждённой суммы или операции.")
    if PaymentRefundAttempt.objects.filter(payment=payment).exists():
        raise RefundUnavailable("Запрос возврата уже создан. Повторная отправка запрещена.")

    try:
        bank = get_tochka_operation(payment.operation_id)
    except (AcquiringUnavailable, OSError, ValueError) as exc:
        raise RefundUnavailable("Не удалось проверить статус в Точке. Деньги не возвращались.") from exc
    _verify_bank_payment(payment, bank)

    # The database unique constraint persists a no-retry barrier before touching
    # the bank. A concurrent owner click can never send a second refund.
    try:
        with transaction.atomic():
            locked = SubscriptionPayment.objects.select_for_update().get(pk=payment.pk)
            if locked.status != "paid" or locked.operation_id != payment.operation_id:
                raise RefundUnavailable("Статус платежа изменился. Обновите список.")
            if PaymentRefundAttempt.objects.filter(payment=locked).exists():
                raise RefundUnavailable("Возврат уже запрошен. Повторная отправка запрещена.")
            attempt = PaymentRefundAttempt.objects.create(
                payment=locked, requested_by=actor,
                bank_operation_id=locked.operation_id,
                amount_kopeks=locked.amount_kopeks,
                status="sending", bank_status="APPROVED",
            )
    except IntegrityError as exc:
        # A second concurrent HTTP request can lose the unique constraint
        # race. It must never proceed to the bank POST.
        raise RefundUnavailable("Возврат уже запрошен. Повторная отправка запрещена.") from exc

    log.info("nosale_refund_requested payment=%s attempt=%s actor=%s", payment.pk, attempt.pk, actor.pk)
    try:
        reply = _send_full_refund(payment)
    except RefundUnavailable:
        log.warning("nosale_refund_uncertain payment=%s attempt=%s", payment.pk, attempt.pk)
        return _update_attempt(attempt, status="uncertain", failure_code="bank_reply_uncertain")

    # The official response identifies the child refund operation: isRefund,
    # operationId, amount and orderId. HTTP 200 alone cannot prove a refund.
    try:
        amount_matches = _kopeks(reply.get("amount")) == payment.amount_kopeks
    except RefundUnavailable:
        amount_matches = False
    if (reply.get("isRefund") is not True
            or reply.get("operationId") != payment.operation_id
            or not amount_matches
            or not reply.get("orderId")):
        return _update_attempt(attempt, status="uncertain", failure_code="bank_reply_mismatch")

    order_id = str(reply["orderId"])
    # POST accepted a child refund order, but the parent status may still be
    # APPROVED for a short period. Never invent ON-REFUND before real readback.
    _update_attempt(attempt, status="submitted", bank_status="APPROVED", order_id=order_id)
    try:
        # This is a signed-identity-checked independent readback. A full refund
        # only revokes access when the bank's parent operation says REFUNDED.
        outcome = reconcile_payment(payment)
    except (AcquiringUnavailable, InvalidWebhook, OSError, ValueError) as exc:
        log.warning("nosale_refund_readback_uncertain payment=%s kind=%s", payment.pk, type(exc).__name__)
        return attempt

    payment.refresh_from_db()
    if payment.status == "refunded":
        return _update_attempt(attempt, status="confirmed", bank_status="REFUNDED", order_id=order_id)
    return attempt


def reconcile_refund_attempt(payment, bank_status):
    """Called from the periodic bank readback, never POSTs a second refund."""
    attempt = PaymentRefundAttempt.objects.filter(payment=payment).first()
    if attempt is None:
        return
    if bank_status == "REFUNDED" and payment.status == "refunded":
        if attempt.status != "confirmed":
            _update_attempt(attempt, status="confirmed", bank_status=bank_status)
    elif bank_status == "ON-REFUND":
        if attempt.status in ("sending", "submitted", "uncertain"):
            _update_attempt(attempt, status="submitted", bank_status=bank_status)
    elif bank_status == "REFUNDED_PARTIALLY" and attempt.status != "confirmed":
        # NoSale currently supports full refunds only; partial ones require
        # operator review and must not be presented as a confirmed refund.
        _update_attempt(
            attempt, status="uncertain", bank_status=bank_status,
            failure_code="partial_refund_manual_review",
        )
