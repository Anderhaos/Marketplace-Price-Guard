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
from .finance import (
    FinanceUnavailable, MAX_DETAIL_ROWS, MAX_DETAIL_STORED_ROWS,
    fetch_reports_list, fetch_report_detail_page, require_finance_service_token,
)
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
                snapshot.detail_report_id = None
                snapshot.detail_rows = []
                snapshot.detail_fetched_at = None
                snapshot.detail_partial = True
                # WB's quota belongs to the seller, not the credential. A
                # rotated token must never reset a running rate cooldown.
                previous_cooldown = snapshot.next_refresh_at
                snapshot.next_refresh_at = previous_cooldown if previous_cooldown and previous_cooldown > timezone.now() else None
                snapshot.locked_until = None
                snapshot.last_error = ""
                snapshot.save()
            messages.info(request, "Отдельный финансовый токен сохранён зашифрованным. Проверка доступа произойдёт при обновлении.")
        elif action in ("refresh", "detail", "detail_more"):
            now = timezone.now()
            with transaction.atomic():
                get_user_model().objects.select_for_update().get(pk=request.user.pk)
                snapshot = FinanceSnapshot.objects.select_for_update().filter(account=store).first()
                if not snapshot or not snapshot.encrypted_finance_token or not snapshot.consented_at:
                    messages.error(request, "Сначала подключите отдельный сервисный токен категории «Финансы».")
                    return redirect("finance_audit")
                selected_report_id = None
                if action == "detail":
                    raw_id = request.POST.get("report_id", "")
                    if not raw_id.isascii() or not raw_id.isdecimal() or len(raw_id) > 19:
                        messages.error(request, "Неизвестный финансовый отчёт.")
                        return redirect("finance_audit")
                    selected_report_id = int(raw_id)
                    if not 0 < selected_report_id < 2**63 or not snapshot.fetched_at or not any(
                        item.get("report_id") == selected_report_id for item in snapshot.reports
                    ):
                        messages.error(request, "Выберите отчёт из сохранённого списка.")
                        return redirect("finance_audit")
                cursor = 0
                if action == "detail_more":
                    # The server, NOT the browser, selects the report and cursor.
                    selected_report_id = snapshot.detail_report_id
                    prior = snapshot.detail_rows
                    if (not snapshot.fetched_at or not snapshot.detail_fetched_at
                            or not snapshot.detail_partial
                            or type(selected_report_id) is not int or not 0 < selected_report_id < 2**63
                            or not any(r.get("report_id") == selected_report_id for r in snapshot.reports)
                            or not isinstance(prior, list) or not prior
                            or len(prior) + MAX_DETAIL_ROWS > MAX_DETAIL_STORED_ROWS
                            or not isinstance(prior[-1], dict)
                            or type(prior[-1].get("rrd_id")) is not int
                            or not 0 < prior[-1]["rrd_id"] < 2**63):
                        messages.info(request, "Догрузка недоступна: отчёт завершён или достигнут безопасный предел.")
                        return redirect("finance_audit")
                    cursor = prior[-1]["rrd_id"]
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
                if action == "detail":
                    summaries, partial = fetch_report_detail_page(token, selected_report_id)
                elif action == "detail_more":
                    summaries, partial = fetch_report_detail_page(token, selected_report_id, cursor)
                else:
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
                current = FinanceSnapshot.objects.filter(
                    pk=snapshot.pk, encrypted_finance_token=snapshot.encrypted_finance_token,
                    consented_at=snapshot.consented_at,
                )
                if action in ("detail", "detail_more"):
                    current = current.filter(fetched_at=snapshot.fetched_at)
                    if action == "detail_more":
                        # Also require the exact previous evidence version.
                        current = current.filter(
                            detail_report_id=selected_report_id,
                            detail_fetched_at=snapshot.detail_fetched_at,
                        )
                        combined = snapshot.detail_rows + summaries
                        if len(combined) > MAX_DETAIL_STORED_ROWS:
                            messages.error(request, "Превышен предел сохранённых строк WB.")
                        else:
                            published = current.update(
                                detail_rows=combined, detail_partial=partial,
                                detail_fetched_at=timezone.now(), last_error="",
                                locked_until=None,
                            )
                            if published:
                                messages.info(request, "Догружена страница. Полнота доказана только ответом WB 204; финансирование скидок и прибыль неизвестны.")
                    else:
                        published = current.update(
                            detail_report_id=selected_report_id, detail_rows=summaries,
                            detail_partial=partial, detail_fetched_at=timezone.now(),
                            last_error="", locked_until=None,
                        )
                        if published:
                            messages.info(request, "Загружена только первая страница детализации. Вывод о финансировании скидки и прибыли не подтверждён.")
                else:
                    current.update(
                        reports=summaries, partial=partial, fetched_at=timezone.now(),
                        detail_report_id=None, detail_rows=[], detail_fetched_at=None,
                        detail_partial=True, last_error="", locked_until=None,
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
        "detail_rows": snapshot.detail_rows if snapshot else [],
        "can_refresh": bool(enabled and snapshot and snapshot.encrypted_finance_token
                            and (not snapshot.next_refresh_at or snapshot.next_refresh_at <= timezone.now())
                            and (not snapshot.locked_until or snapshot.locked_until <= timezone.now())),
        "can_continue": bool(
            enabled and snapshot and snapshot.encrypted_finance_token
            and snapshot.detail_fetched_at and snapshot.detail_partial
            and snapshot.detail_report_id
            and isinstance(snapshot.detail_rows, list) and snapshot.detail_rows
            and len(snapshot.detail_rows) + MAX_DETAIL_ROWS <= MAX_DETAIL_STORED_ROWS
            and (not snapshot.next_refresh_at or snapshot.next_refresh_at <= timezone.now())
            and (not snapshot.locked_until or snapshot.locked_until <= timezone.now())
        ),
        "detail_page_limit": MAX_DETAIL_STORED_ROWS,
    })
