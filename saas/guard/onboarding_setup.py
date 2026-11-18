"""Fail-closed, local-only first-run Guard setup before the 3-day trial.

A successful WB import is not a paid capability or a trial grant. The seller
configures limits here; only explicit activation grants a one-time trial.
No WB/provider calls are made by setup GET/POST.
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F
from django.http import HttpResponseNotAllowed
from django.utils import timezone
from django.shortcuts import redirect, render

from billing.models import AccessGrant
from billing.trial import grant_trial_once
from .forms import DefaultDiscountForm
from .models import GuardAccount, Product


MAX_SETUP_PRODUCTS = 100


def render_or_process_setup(request, account, token_can_write, scheduled_wb_due):
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])
    products = list(
        Product.objects.filter(account=account, active=True)
        .order_by("vendor_code", "nm_id")[:MAX_SETUP_PRODUCTS + 1]
    )
    visible = products[:MAX_SETUP_PRODUCTS]
    more_products = len(products) > MAX_SETUP_PRODUCTS

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "retry_empty_import":
            if visible or not settings.SAAS_WB_NETWORK_ENABLED:
                messages.error(request, "Повторная загрузка сейчас недоступна.")
                return redirect("dashboard")
            with transaction.atomic():
                get_user_model().objects.select_for_update().get(pk=request.user.pk)
                current = GuardAccount.objects.select_for_update().get(
                    pk=account.pk, user_id=request.user.pk
                )
                if (AccessGrant.objects.filter(user_id=request.user.pk).exists()
                        or current.enabled or not current.token_ciphertext
                        or Product.objects.filter(account=current, active=True).exists()):
                    messages.error(request, "Состояние магазина изменилось.")
                elif current.next_check_at > timezone.now():
                    messages.info(request, "Ожидаем разрешённого времени следующей проверки WB.")
                else:
                    current.check_requested = True
                    current.next_check_at = timezone.now()
                    current.save(update_fields=["check_requested", "next_check_at"])
                    messages.success(request, "Повторная загрузка товаров поставлена в очередь.")
            return redirect("dashboard")
        if action not in ("save_setup", "start_trial"):
            return HttpResponseNotAllowed(["GET", "POST"])
        form = DefaultDiscountForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Общий предел скидки должен быть от 0 до 99%.")
            return redirect("dashboard")
        default_limit = form.cleaned_data["default_allowed_discount"]
        if default_limit == 0 and request.POST.get("confirm_zero_discount") != "1":
            messages.error(request, "Для нулевого лимита подтвердите отключение всех скидок.")
            return redirect("dashboard")
        override_values = {}
        for product in visible:
            raw = request.POST.get(f"limit_{product.nm_id}")
            if raw is None:
                messages.error(request, "Форма настройки товаров устарела. Обновите страницу.")
                return redirect("dashboard")
            if raw == "":
                override_values[product.pk] = None
            else:
                try:
                    numeric = int(raw)
                except (ValueError, TypeError):
                    numeric = -1
                if not 0 <= numeric <= 99 or raw.strip() != str(numeric):
                    messages.error(request, "Индивидуальный предел скидки должен быть от 0 до 99%.")
                    return redirect("dashboard")
                override_values[product.pk] = numeric

        if action == "start_trial":
            if not settings.SAAS_WB_NETWORK_ENABLED or settings.SAAS_WB_READ_ONLY:
                messages.error(request, "Защита WB сейчас отключена оператором. Настройки можно сохранить без запуска.")
                return redirect("dashboard")
            if not settings.SAAS_WB_TOKEN_VALIDATION_ENABLED or not token_can_write:
                messages.error(request, "Для запуска защиты нужен проверенный токен с правом изменения скидок.")
                return redirect("dashboard")
            if not visible:
                messages.error(request, "Сначала дождитесь появления товаров WB. Пробный период не начался.")
                return redirect("dashboard")

        # User lock serializes double-submit and concurrent bank grant activity.
        # Onboarding does not import/alter any external WB data.
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            current = GuardAccount.objects.select_for_update().get(
                pk=account.pk, user_id=request.user.pk
            )
            if AccessGrant.objects.filter(user_id=request.user.pk).exists():
                messages.info(request, "Пробный период уже использован или доступ выдан ранее.")
                return redirect("dashboard")
            if (not current.token_ciphertext
                    or current.initial_sync_completed_at is None
                    or current.enabled):
                messages.error(request, "Подключение магазина изменилось. Повторите настройку.")
                return redirect("dashboard")
            if set(override_values) != set(
                Product.objects.filter(pk__in=override_values, account=current,
                                       active=True).values_list("pk", flat=True)
            ):
                messages.error(request, "Товары обновились. Откройте настройки ещё раз.")
                return redirect("dashboard")
            changed = current.default_allowed_discount != default_limit
            if changed:
                current.default_allowed_discount = default_limit
            for product in visible:
                new_limit = override_values[product.pk]
                if product.allowed_discount_override != new_limit:
                    Product.objects.filter(pk=product.pk, account=current).update(
                        allowed_discount_override=new_limit
                    )
                    changed = True
            if changed:
                current.rules_version = F("rules_version") + 1
            if action == "start_trial":
                # Recheck hard safety gates while holding the account lock.
                if (settings.SAAS_WB_READ_ONLY or not settings.SAAS_WB_NETWORK_ENABLED
                        or not Product.objects.filter(account=current, active=True).exists()):
                    messages.error(request, "Запуск защиты сейчас невозможен.")
                    return redirect("dashboard")
                now = timezone.now()
                current.enabled = True
                current.check_requested = True
                current.next_check_at = scheduled_wb_due(current, now)
                grant, created = grant_trial_once(request.user.pk, at=now)
                if not created:
                    messages.error(request, "Пробный период уже был активирован.")
                    return redirect("dashboard")
            current.save()
        if action == "start_trial":
            messages.success(request, "Защита включена. Три бесплатных дня начались.")
        else:
            messages.success(request, "Лимиты сохранены только в NoSale. WB не изменён.")
        return redirect("dashboard")

    return render(request, "guard/onboarding_rules.html", {
        "account": account,
        "setup_products": visible,
        "more_products": more_products,
        "retry_empty_import_available": bool(
            not visible and settings.SAAS_WB_NETWORK_ENABLED
            and account.next_check_at <= timezone.now()
        ),
        "next_retry_at": account.next_check_at,
        "setup_count": Product.objects.filter(account=account, active=True).count(),
        "can_start_protection": bool(
            visible and token_can_write and settings.SAAS_WB_NETWORK_ENABLED
            and settings.SAAS_WB_TOKEN_VALIDATION_ENABLED
            and not settings.SAAS_WB_READ_ONLY
        ),
        "onboarding_required": True,
    })
