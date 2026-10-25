"""Seller-owned, explicit-consent, manually refreshed read-only WB Finance."""
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.utils import timezone

from billing.access import has_plan_capability
from .crypto import decrypt_token, encrypt_token
from .finance import FinanceUnavailable, fetch_reports_list, require_finance_service_token
from .models import FinanceSnapshot, FinanceRateGate
from .stores import selected_store


@login_required
def finance_audit(request):
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])
    store = selected_store(request)
    entitled = has_plan_capability(request.user.pk, "discount_guard")
    existing = FinanceSnapshot.objects.filter(account=store).first()
    can_revoke = request.method == "POST" and request.POST.get("action") == "revoke"
    if store.marketplace != "wb" or (not entitled and not (can_revoke or (
        request.method == "GET" and existing and existing.encrypted_finance_token
    ))):
        return render(request, "guard/assistant_access.html", {
            "access_title": "Финансовые отчёты доступны с активным доступом",
            "access_explanation": "Выберите принадлежащий вам магазин WB и действующий доступ.",
        }, status=403)
    if request.method == "GET" and not entitled:
        # Privacy: after the tariff expires, show only self-service data removal.
        return render(request, "guard/finance.html", {
            "account": store, "finance_enabled": False,
            "connected": bool(existing and existing.encrypted_finance_token),
            "snapshot": None, "reports": [], "can_refresh": False,
            "access_expired": True,
        })
    enabled = bool(settings.SAAS_WB_NETWORK_ENABLED and settings.NOSALE_WB_FINANCE_ENABLED)
    if request.method == "POST":
        action = request.POST.get("action")
        # Revocation is always available, even during an operator shutdown or
        # an expired subscription. A network kill switch must never trap data.
        if action == "revoke":
            with transaction.atomic():
                get_user_model().objects.select_for_update().get(pk=request.user.pk)
                FinanceSnapshot.objects.filter(account=store).delete()
            messages.success(request, "Финансовый доступ и сохранённые сведения удалены.")
            return redirect("finance_audit")
        if not enabled:
            messages.info(request, "Финансовый API WB отключён оператором NoSale.")
            return redirect("finance_audit")
        if action == "connect":
            if request.POST.get("finance_consent") != "yes":
                messages.error(request, "Нужно отдельно подтвердить доступ к финансовым данным.")
                return redirect("finance_audit")
            try:
                token = require_finance_service_token(request.POST.get("finance_token", ""))
            except FinanceUnavailable as exc:
                messages.error(request, str(exc))
                return redirect("finance_audit")
            with transaction.atomic():
                get_user_model().objects.select_for_update().get(pk=request.user.pk)
                snapshot, _ = FinanceSnapshot.objects.select_for_update().get_or_create(account=store)
                snapshot.encrypted_finance_token = encrypt_token(token)
                snapshot.token_hint = "сервисный · Финансы"
                snapshot.consented_at = timezone.now()
                snapshot.reports = []
                snapshot.partial = True
                snapshot.fetched_at = None
                # WB's quota belongs to the seller, not the credential. A
                # rotated token must never reset a running rate cooldown.
                previous_cooldown = snapshot.next_refresh_at
                snapshot.next_refresh_at = previous_cooldown if previous_cooldown and previous_cooldown > timezone.now() else None
                snapshot.locked_until = None
                snapshot.last_error = ""
                snapshot.save()
            messages.info(request, "Отдельный финансовый токен сохранён зашифрованным. Проверка доступа произойдёт при обновлении.")
        elif action == "refresh":
            now = timezone.now()
            with transaction.atomic():
                get_user_model().objects.select_for_update().get(pk=request.user.pk)
                snapshot = FinanceSnapshot.objects.select_for_update().filter(account=store).first()
                if not snapshot or not snapshot.encrypted_finance_token or not snapshot.consented_at:
                    messages.error(request, "Сначала подключите отдельный сервисный токен категории «Финансы».")
                    return redirect("finance_audit")
                # WB imposes a limit per seller, not per store or token alias.
                gate, _ = FinanceRateGate.objects.select_for_update().get_or_create(
                    user=request.user,
                )
                # Keep compatibility with a previously scheduled store
                # cooldown as well as the durable per-user Finance gate.
                if ((gate.next_allowed_at and gate.next_allowed_at > now)
                        or FinanceSnapshot.objects.filter(
                            account__user_id=request.user.pk, next_refresh_at__gt=now
                        ).exists()):
                    messages.info(request, "Лимит WB Finance: повторите проверку позже.")
                    return redirect("finance_audit")
                if snapshot.locked_until and snapshot.locked_until > now:
                    messages.info(request, "Финансовая проверка уже выполняется.")
                    return redirect("finance_audit")
                gate.next_allowed_at = now + timedelta(seconds=75)
                gate.save(update_fields=["next_allowed_at"])
                snapshot.locked_until = now + timedelta(minutes=2)
                snapshot.next_refresh_at = gate.next_allowed_at
                snapshot.save(update_fields=["locked_until", "next_refresh_at"])
            try:
                token = decrypt_token(snapshot.encrypted_finance_token)
                end = now.date()
                start = end - timedelta(days=30)
                summaries, partial = fetch_reports_list(
                    token, start.isoformat(), end.isoformat()
                )
            except (ValueError, FinanceUnavailable):
                FinanceSnapshot.objects.filter(
                    pk=snapshot.pk, encrypted_finance_token=snapshot.encrypted_finance_token,
                    consented_at=snapshot.consented_at,
                ).update(
                    locked_until=None,
                    last_error="WB не предоставил финансовый отчёт. Проверьте токен и попробуйте позже.",
                )
                messages.error(request, "Финансовую сверку не удалось обновить; старые подтверждённые данные не изменены.")
            else:
                # Token rotation/revocation during a slow upstream request must
                # never publish evidence produced under the former consent.
                FinanceSnapshot.objects.filter(
                    pk=snapshot.pk, encrypted_finance_token=snapshot.encrypted_finance_token,
                    consented_at=snapshot.consented_at,
                ).update(
                    reports=summaries, partial=partial, fetched_at=timezone.now(),
                    last_error="", locked_until=None,
                )
                messages.success(request, "Получен список отчётов WB. Источник финансирования конкретной акции по нему не определяется.")
        else:
            return HttpResponseNotAllowed(["GET"])
        return redirect("finance_audit")

    snapshot = FinanceSnapshot.objects.filter(account=store).first()
    return render(request, "guard/finance.html", {
        "account": store,
        "finance_enabled": enabled,
        "connected": bool(snapshot and snapshot.encrypted_finance_token),
        "snapshot": snapshot,
        "reports": snapshot.reports if snapshot else [],
        "can_refresh": bool(enabled and snapshot and snapshot.encrypted_finance_token
                            and (not snapshot.next_refresh_at or snapshot.next_refresh_at <= timezone.now())
                            and (not snapshot.locked_until or snapshot.locked_until <= timezone.now())),
    })
