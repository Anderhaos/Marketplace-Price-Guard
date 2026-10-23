"""Read-only store recommendations from observed WB seller data.

No LLM, cross-seller information, inferred margin or unverified promotion funding.
Output is suitable as the factual context for a future constrained AI narrator.
"""
from datetime import timedelta

from django.utils import timezone

from .models import GuardEvent, Product, Upload


def store_recommendations(account, *, now=None):
    now = now or timezone.now()
    results = []
    metrics = {"active_products": 0, "stale_products": 0, "over_limit": 0, "discount_changes_24h": 0, "price_changes_24h": 0}
    # A failed WB check can update last_check_at without refreshing products.
    # Report the time of the actual observed product snapshot instead.
    last_observed_at = None

    def add(priority, title, detail, *, observed_at=None, source="NoSale"):
        results.append({
            "priority": priority, "title": title, "detail": detail,
            "source": source, "observed_at": observed_at,
        })

    if not account.token_ciphertext:
        add(0, "Подключите Wildberries", "Для персональных советов нужны данные вашего магазина WB.")
        return {"recommendations": results, "metrics": metrics, "last_observed_at": None}

    if account.initial_sync_completed_at is None and not account.last_check_at:
        add(0, "Дождитесь первой синхронизации", "После получения данных WB появятся рекомендации по товарам и событиям.")

    if account.last_error:
        add(0, "Проверьте синхронизацию WB", "Последняя проверка завершилась с ошибкой. Данные магазина могут быть устаревшими.", observed_at=account.last_check_at)

    # Iteration is bounded in memory even when the connected store has many SKUs.
    age_limit = timedelta(minutes=max(30, 2 * account.interval_minutes))
    freshness_cutoff = now - age_limit
    over_limit_examples = []
    for item in Product.objects.filter(account=account, active=True).only(
        "nm_id", "title", "current_discount", "allowed_discount_override", "checked_at"
    ).iterator(chunk_size=500):
        metrics["active_products"] += 1
        if item.checked_at and (last_observed_at is None or item.checked_at > last_observed_at):
            last_observed_at = item.checked_at
        if item.checked_at is None or item.checked_at < freshness_cutoff:
            metrics["stale_products"] += 1
        limit = item.allowed_discount_override
        if limit is None:
            limit = account.default_allowed_discount
        if item.current_discount > limit:
            metrics["over_limit"] += 1
            gap = item.current_discount - limit
            entry = (gap, item.nm_id, item.current_discount, limit, item.title[:110], item.checked_at)
            over_limit_examples.append(entry)
            over_limit_examples.sort(key=lambda x: (-x[0], x[1]))
            del over_limit_examples[4:]

    is_stale = not last_observed_at or metrics["stale_products"] > 0
    if metrics["stale_products"]:
        add(
            1, "Перепроверьте устаревшие товары",
            f"Данные {metrics['stale_products']} из {metrics['active_products']} товаров старше ожидаемого интервала. "
            "Нельзя считать весь магазин проверенным только по одному обновлённому товару.",
            observed_at=last_observed_at, source="NoSale: время последнего снимка по SKU",
        )
    elif is_stale and last_observed_at:
        add(1, "Обновите данные магазина", "С последнего подтверждённого снимка WB прошло больше обычного интервала. Цены и скидки могли измениться.", observed_at=last_observed_at)

    if metrics["over_limit"]:
        for gap, nm_id, discount, limit, title, checked_at in over_limit_examples:
            add(
                1,
                f"Проверьте товар WB {nm_id}",
                f"По последним данным скидка {discount}%, ваш предел {limit}% (превышение на {gap} п. п.). "
                "Это нарушение заданного ограничения, но не подтверждение финансового убытка.",
                observed_at=checked_at, source="WB API: цены и скидки",
            )

    pending = Upload.objects.filter(account=account, status="pending").count()
    if pending:
        add(1, "Проверьте подтверждение операций", f"У {pending} запросов на изменение скидок ещё нет подтверждённого результата WB. Не считайте их выполненными до проверки.", observed_at=last_observed_at, source="NoSale: журнал операций WB")

    since = now - timedelta(hours=24)
    events = GuardEvent.objects.filter(account=account, created_at__gte=since)
    for kind, key, title, detail in (
        ("price_changed", "price_changes_24h", "Перепроверьте изменившиеся цены",
         "За сутки зафиксировано {count} изменений цены. Сверьте важные товары с вашей ценовой стратегией."),
        ("discount_changed", "discount_changes_24h", "Проверьте изменения скидок",
         "За сутки зафиксировано {count} изменений скидки. Посмотрите историю затронутых товаров."),
    ):
        count = events.filter(kind=kind).count()
        metrics[key] = count
        if count:
            add(2, title, detail.format(count=count), observed_at=last_observed_at, source="NoSale: история наблюдений WB")

    if not results and metrics["active_products"] and not is_stale:
        add(3, "Новых отклонений не обнаружено",
            "По последним загруженным данным нет превышений установленной скидки или новых зафиксированных изменений. "
            "Это не оценка прибыльности акций.", observed_at=last_observed_at, source="WB API: цены и скидки")
    if not results:
        add(3, "Недостаточно актуальных данных",
            "Рекомендации появятся после подтверждённой загрузки товаров и истории WB.")

    return {
        "recommendations": sorted(results, key=lambda row: row["priority"])[:8],
        "metrics": metrics,
        "last_observed_at": last_observed_at,
        "is_stale": is_stale,
        "pending_uploads": pending,
    }
