"""NoSale Standard assistant: bounded factual answers, no LLM and no WB calls.

Reuses the intent/context separation of existing MiniBook assistant without
Telegram transport, CRM commands or speculative marketplace analytics.
Smart will use the same UI with its own gated evidence-based AI layer.
"""
from datetime import timedelta

from django.db.models import F, Q
from django.utils import timezone

from billing.trial import access_snapshot
from .models import GuardEvent, Product, Upload


def _time(value):
    if value is None:
        return "нет подтверждённой проверки"
    return timezone.localtime(value).strftime("%d.%m.%Y в %H:%M")


def basic_assistant_reply(user_id, account, question, *, now=None):
    """Read-only. Caller MUST enforce account.user_id=user_id and active grant."""
    if account.user_id != user_id:
        raise PermissionError("Store does not belong to the seller")
    now = now or timezone.now()
    q = " ".join(question.casefold().replace("ё", "е").split())

    if any(k in q for k in ("подписк", "тариф", "оплат", "пробн", "срок доступа")):
        access = access_snapshot(user_id, at=now)
        if not access["active"]:
            return "Оплаченного или пробного доступа сейчас нет. Откройте «Подписка», чтобы посмотреть доступные тарифы."
        return (
            f"Сейчас действует {access['active_plan'].capitalize()} до {_time(access['expires_at'])}. "
            "Текущий тариф и историю платежей можно проверить в разделе «Подписка»."
        )

    if any(k in q for k in ("акци", "за счет вб", "за счет wb", "прибыл", "маржин", "окуп")):
        return (
            "По одной цене или скидке нельзя определить, кто финансирует акцию WB и прибыльна ли она. "
            "Для такого вывода нужны подтверждённые данные о финансировании и расходах. "
            "Сейчас NoSale защищает установленный вами предел скидки."
        )

    if any(k in q for k in ("превыш", "лимит", "скидк", "предел")):
        base = Product.objects.filter(account=account, active=True)
        above = base.filter(
            Q(allowed_discount_override__isnull=True, current_discount__gt=account.default_allowed_discount)
            | Q(allowed_discount_override__isnull=False, current_discount__gt=F("allowed_discount_override"))
        ).count()
        return (
            f"По последним сохранённым данным магазина «{account.name}»: товаров выше заданного предела скидки — {above}. "
            f"Общий предел магазина — {account.default_allowed_discount}%. "
            "Для отдельных товаров может действовать свой предел. Сверьте дату проверки и откройте «Товары»."
        )

    if any(k in q for k in ("товар", "артикул", "сколько позиц", "сколько sku")):
        count = Product.objects.filter(account=account, active=True).count()
        return (
            f"У магазина «{account.name}» сейчас {count} активных товаров в последней сохранённой выборке NoSale. "
            "Это не онлайн-остатки и не число продаж WB."
        )

    if any(k in q for k in ("цен", "событ", "измен", "истори", "уведом", "оповещ")):
        since = now - timedelta(hours=24)
        events = GuardEvent.objects.filter(account=account, created_at__gte=since)
        prices = events.filter(kind="price_changed").count()
        discounts = events.filter(kind="discount_changed").count()
        errors = events.filter(kind__in=["error", "upload_error"]).count()
        return (
            f"За последние 24 часа NoSale записал по магазину «{account.name}»: "
            f"изменений цены — {prices}, изменений скидки — {discounts}, ошибок — {errors}. "
            "Это журнал NoSale, а не полный финансовый отчёт WB. Подробности есть в «История»."
        )

    if any(k in q for k in ("ошиб", "синхр", "провер", "работает", "статус", "подключ")):
        failures = "Есть ошибка последней проверки; данные могут быть устаревшими." if account.last_error else "Ошибка последней проверки не отмечена."
        token = "Токен WB подключён." if account.token_ciphertext else "Токен WB ещё не подключён."
        running = "Автозащита включена." if account.enabled else "Автозащита выключена."
        pending = Upload.objects.filter(account=account, status="pending").count()
        return (
            f"Магазин «{account.name}». {token} {running} "
            f"Последняя попытка проверки: {_time(account.last_check_at)}. {failures} "
            f"Операций с ожидаемым подтверждением WB: {pending}. "
            "Успешная проверка товаров может быть старше последней попытки подключения."
        )

    if any(k in q for k in ("настро", "как включ", "как помен", "правил", "как защит")):
        return (
            "Откройте «Настройки», чтобы задать общий предел скидки и параметры проверки. "
            "Индивидуальный предел товара меняется в разделе «Товары». "
            "Защита требует действующего доступа, подключения WB и разрешённых операций."
        )

    return (
        "Могу объяснить работу NoSale и показать сохранённые сведения вашего магазина. "
        "Спросите: «Как работает защита?», «Статус проверки», «Сколько товаров выше лимита?», "
        "«Что изменилось за сутки?», «Есть ли ошибки?» или «Какой у меня тариф?». "
        "Расширенные рекомендации по магазину входят в Smart."
    )
