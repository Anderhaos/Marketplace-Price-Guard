"""Seller-only UI for read-only promotion-calendar evidence."""
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.utils import timezone

from billing.access import has_plan_capability
from .crypto import decrypt_token
from .models import Product, PromotionSnapshot
from .promotions import inspect_basic_promotion_step, inspect_promotions
from .promotion_presentation import build_promotion_presentation
from .stores import selected_store
from .wb import WBApiError, WBClient, get_token_permissions, get_token_type


@login_required
def promotion_audit(request):
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])
    account = selected_store(request)
    if not has_plan_capability(request.user.pk, "promotion_audit"):
        return render(request, "guard/assistant_access.html", {
            "access_title": "Контроль акций доступен с активной подпиской",
            "access_explanation": "Для просмотра акций требуется действующий пробный или оплаченный доступ.",
        }, status=403)
    if request.method == "POST":
        if request.POST.get("action") != "refresh":
            return HttpResponseNotAllowed(["GET"])
        if account.marketplace != "wb" or not account.token_ciphertext:
            messages.error(request, "Сначала подключите магазин Wildberries.")
            return redirect("promotion_audit")
        if not settings.SAAS_WB_NETWORK_ENABLED:
            messages.info(request, "В публичном staging сетевой WB API отключён. Данные не обновлялись.")
            return redirect("promotion_audit")
        try:
            token = decrypt_token(account.token_ciphertext)
        except (ValueError, TypeError):
            messages.error(request, "Не удалось прочитать токен магазина. Проверьте подключение.")
            return redirect("promotion_audit")
        permission = get_token_permissions(token)
        if not permission or "Цены и скидки" not in permission["categories"]:
            messages.error(request, "Токену WB нужна категория «Цены и скидки».")
            return redirect("promotion_audit")
        token_type = get_token_type(token)
        now = timezone.now()
        # Basic tokens have a shared WB calendar quota of one call per hour.
        cooldown = timedelta(minutes=65 if token_type == "base" else 15)
        with transaction.atomic():
            snapshot, _ = PromotionSnapshot.objects.get_or_create(account=account)
            snapshot = PromotionSnapshot.objects.select_for_update().get(pk=snapshot.pk)
            if (snapshot.locked_until and snapshot.locked_until > now):
                messages.info(request, "Проверка акций уже выполняется.")
                return redirect("promotion_audit")
            if snapshot.next_refresh_at and snapshot.next_refresh_at > now:
                messages.info(request, "Лимит WB: следующая проверка будет доступна позже.")
                return redirect("promotion_audit")
            snapshot.locked_until = now + timedelta(minutes=3)
            snapshot.next_refresh_at = now + cooldown
            snapshot.save(update_fields=["locked_until", "next_refresh_at"])
        try:
            active_products = {
                product.nm_id: product for product in Product.objects.filter(account=account, active=True)
            }
            wb = WBClient(token)
            if token_type == "base":
                # Respect the basic-token *shared* 1-request/hour calendar quota.
                # Never burst list + details + participant calls in one request.
                evidence = inspect_basic_promotion_step(
                    wb, cached_promotions=snapshot.promotions,
                    calendar_fetched_at=snapshot.fetched_at,
                    store_products=active_products, at=now,
                )
            else:
                evidence = inspect_promotions(
                    wb, token_type=token_type, at=now,
                    store_products=active_products,
                )
        except WBApiError as exc:
            # Never expose credential, WB raw response or arbitrary user data.
            PromotionSnapshot.objects.filter(pk=snapshot.pk).update(
                locked_until=None,
                last_error=(
                    "WB ограничил частоту запросов. Попробуйте позже." if exc.status == 429
                    else "WB не предоставил данные календаря. Проверьте доступ и повторите позже."
                ),
            )
            messages.error(request, "Календарь WB пока не удалось обновить. Последняя сохранённая проверка не изменена.")
        else:
            PromotionSnapshot.objects.filter(pk=snapshot.pk).update(
                promotions=evidence["promotions"][:40],
                partial=evidence["partial"],
                fetched_at=(now if evidence.get("phase") in ("calendar", "full")
                            else snapshot.fetched_at),
                last_error="", locked_until=None,
            )
            messages.success(request, {
                "details": "Получены агрегированные сведения WB об участии продавца. Цены не изменялись.",
                "participants": "Получен состав одной обычной акции WB. Цены не изменялись.",
                "candidates": "Получены товары, доступные для вступления в обычную акцию. Участие не изменялось.",
                "calendar": "Календарь WB обновлён. Цены не изменялись.",
            }.get(evidence.get("phase"), "Данные акций WB обновлены. Цены не изменялись."))
        return redirect("promotion_audit")

    snapshot = PromotionSnapshot.objects.filter(account=account).first()
    products = list(Product.objects.filter(account=account, active=True))
    presentation = build_promotion_presentation(
        snapshot.promotions if snapshot else [],
        selected_filter=request.GET.get("filter", "all"),
    )
    return render(request, "guard/promotions.html", {
        **presentation,
        "catalog_total": len(products),
        "catalog_over_limit": sum(p.current_discount > p.allowed_discount for p in products),
        "account": account,
        "snapshot": snapshot,
        "promotions": snapshot.promotions if snapshot else [],
        "wb_network_enabled": settings.SAAS_WB_NETWORK_ENABLED,
        "can_refresh": bool(
            account.token_ciphertext and settings.SAAS_WB_NETWORK_ENABLED and (
                snapshot is None or (
                    (snapshot.next_refresh_at is None or snapshot.next_refresh_at <= timezone.now())
                    and (snapshot.locked_until is None or snapshot.locked_until <= timezone.now())
                )
            )
        ),
    })
