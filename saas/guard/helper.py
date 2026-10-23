"""NoSale Standard: deterministic, read-only web helper (no model calls).

Intent matching is deliberately narrow. It cannot initiate WB operations, invent
business facts, access another seller's store, or consume AI budget.
"""
from datetime import timedelta

from django.utils import timezone

from billing.trial import access_snapshot
from .models import GuardEvent, Product, Upload


def answer_store_question(account, user_id, question):
    q = " ".join(question.casefold().split())
    if not q or len(q) > 500:
        raise ValueError("Введите вопрос длиной до 500 символов.")
    if account.user_id != user_id:
        raise PermissionError("Магазин не принадлежит пользователю.")
    if any(w in q for w in ("токен", "api-ключ", "пароль", "pin-код", "пин-код", "секрет")):
        return "Ключи и коды доступа не показываются в чате. Управлять подключением WB можно в настройках магазина."
    if any(w in q for w in ("прибыл", "рентабел", "выгод", "окуп", "за счет вб", "за счёт wb", "маржин")):
        return "По одним ценам и скидкам нельзя доказать прибыльность товара или кто финансирует акцию. Для этого понадобятся подтверждённые финансовые данные WB."
    if any(w in q for w in ("подпис", "тариф", "доступ", "оплат")):
        state = access_snapshot(user_id)
        if state["active"]:
            return f"Тариф {state['active_plan'].capitalize()} активен до {timezone.localtime(state['expires_at']):%d.%m.%Y %H:%M}. Оплата и продление — в разделе «Подписка»."
        return "Активной подписки нет. Данные магазина сохранены; для работы защиты откройте раздел «Подписка»."
    if any(w in q for w in ("когда", "провер", "синхрон", "обновлен", "обновл")):
        if account.last_error:
            return "При последней проверке WB возникла ошибка; последние данные могут быть устаревшими. Откройте настройки и историю проверок."
        if not account.last_check_at:
            return "Завершённых проверок WB пока не зафиксировано. После подключения дождитесь первой синхронизации."
        return f"Последняя зафиксированная проверка: {timezone.localtime(account.last_check_at):%d.%m.%Y %H:%M}. Это не гарантирует, что все товары уже обновлены."
    if any(w in q for w in ("предупреж", "событ", "истори", "ошиб")):
        events = list(GuardEvent.objects.filter(account=account).values_list("kind", "created_at")[:5])
        if not events:
            return "В журнале этого магазина пока нет событий. Новые предупреждения появятся в разделе «История»."
        return f"В журнале магазина есть {len(events)} последних событий для просмотра. Откройте «История», чтобы увидеть их причины и детали. Последнее: {events[0][0]}."
    if any(w in q for w in ("товар", "скид", "лимит", "цен", "превыш")):
        active = Product.objects.filter(account=account, active=True)
        count = active.count()
        if not count:
            return "Товары пока не загружены. После синхронизации WB откройте список товаров и настройте допустимые скидки."
        over = sum(
            1 for discount, override in active.values_list("current_discount", "allowed_discount_override").iterator(chunk_size=500)
            if discount > (override if override is not None else account.default_allowed_discount)
        )
        return (f"В последних сохранённых данных магазина {count} активных товаров, "
                f"из них {over} превышают установленный предел скидки. "
                "Это не равно финансовому убытку. Для подробностей откройте «Товары»; расширенные приоритетные рекомендации доступны в Smart.")
    if any(w in q for w in ("защит", "контрол", "работа", "статус")):
        state = "включена" if account.enabled else "выключена"
        return f"Защита магазина «{account.name}» {state}. Установленный предел скидки по умолчанию — {account.default_allowed_discount}%. Проверки зависят от наличия доступа и соединения с WB."
    if any(w in q for w in ("настр", "помог", "как", "начат", "подключ")):
        return "Чтобы начать: подключите WB в настройках, дождитесь загрузки товаров, задайте допустимые скидки и включите защиту. Просматривайте события в «Истории», доступ — в «Подписке»."
    return "Я могу подсказать статус защиты, последнюю проверку, историю предупреждений, товары и скидки, настройку сервиса или подписку. Для расширенных рекомендаций по магазину предусмотрен Smart."
