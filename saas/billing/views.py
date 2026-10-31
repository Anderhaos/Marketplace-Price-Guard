"""Subscription purchase screens and signed Tochka webhook entrypoint."""
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from .models import AccessGrant, SubscriptionPayment
from .plans import tariff_offers
from .service import apply_verified_event, create_checkout
from .tochka import (
    AcquiringUnavailable, InvalidWebhook,
    configured, verify_tochka_webhook,
)
from .trial import access_snapshot

_log = logging.getLogger(__name__)


@login_required
def subscription(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    payments = SubscriptionPayment.objects.filter(user=request.user).order_by("-created_at")[:8]
    return render(request, "billing/subscription.html", {
        "billing_enabled": configured(),
        "tariff_offers": tariff_offers(),
        "subscription_state": access_snapshot(request.user.pk),
        "payments": payments,
    })


@login_required
@require_POST
def checkout(request):
    if not configured():
        messages.info(request, "Приём платежей пока не открыт.")
        return redirect("subscription")
    try:
        payment = create_checkout(request.user.pk, plan=request.POST.get("plan", "standard"))
    except AcquiringUnavailable:
        _log.warning("nosale_billing_link_unavailable")
        messages.error(request, "Платёжная ссылка временно недоступна. Деньги не списаны.")
        return redirect("subscription")
    # Exact provider URL verified by the adapter, never from the request.
    return redirect(payment.payment_url)


@login_required
def payment_return(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    payment_id = request.GET.get("payment", "")
    if not payment_id.isdecimal():
        return redirect("subscription")
    payment = SubscriptionPayment.objects.filter(user=request.user, pk=int(payment_id)).first()
    if payment is None:
        return redirect("subscription")
    # A browser-controlled return URL never activates access.
    paid_grant = AccessGrant.objects.filter(
        source_ref=f"nosale:tochka:{payment.pk}", user=request.user,
        source="payment",
    ).first() if payment.status == "paid" else None
    return render(request, "billing/return.html", {
        "payment": payment, "paid_grant": paid_grant,
        "subscription_state": access_snapshot(request.user.pk),
    })


@csrf_exempt
@require_http_methods(["GET", "HEAD", "POST"])
def tochka_webhook(request):
    if request.method in ("GET", "HEAD"):
        # Needed for Tochka initial endpoint availability checks.
        return HttpResponse(status=200)
    if not configured():
        # Tochka sends a test POST while registering a webhook, and requires
        # HTTP 200. Acknowledge with NO JWT parse, DB write or grant while
        # acquiring is disabled. Disabled seller checkout remains blocked.
        return HttpResponse(status=200)
    if request.content_type != "text/plain":
        return HttpResponse(status=200)
    try:
        event = verify_tochka_webhook(request.body)
        outcome = apply_verified_event(event)
        _log.info("nosale_bank_event_processed outcome=%s", outcome)
    except InvalidWebhook:
        # Never log the JWT/token/body. Acknowledge rejected unsolicited events
        # to avoid excessive retries against a public endpoint.
        _log.warning("nosale_bank_event_rejected")
    except Exception:
        _log.exception("nosale_bank_event_processing_error")
        return HttpResponse(status=503)  # provider retries transient DB outages
    return HttpResponse(status=200)
