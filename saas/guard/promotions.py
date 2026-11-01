"""Strictly read-only, rate-bounded Wildberries seller promotion evidence.

Source: official WB Marketing/Promotion calendar API. A basic token can use
ONE calendar request per hour; never make detail/participant calls for it.
Auto campaigns expose aggregate counts (when details are accessible), but
never individual goods through the nomenclatures endpoint. Funding and
profitability are not available from these endpoints. All unknown is unknown.
"""
from datetime import timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from copy import deepcopy

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .wb import WBApiError


def _rows(payload, key):
    if not isinstance(payload, dict):
        raise WBApiError("WB calendar response has an unexpected structure")
    data = payload.get("data", payload)
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        raise WBApiError("WB calendar response has no expected items")
    return [row for row in data[key] if isinstance(row, dict)]


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _integer(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
        return number if number >= 0 and str(value).strip().isdigit() else None
    except (ValueError, TypeError, OverflowError):
        return None


def _bounded_text(value, limit=160):
    return str(value or "")[:limit]


def _money(value):
    return str(value.quantize(Decimal("0.01")))


def _promotion_details(client, ids):
    details = _rows(client.get_promotion_details(ids), "promotions")
    mapped = {}
    for row in details:
        promo_id = _integer(row.get("id"))
        if promo_id is None or promo_id not in ids:
            continue
        counts = {}
        for key in ("inPromoActionTotal", "inPromoActionLeftovers",
                    "notInPromoActionTotal", "notInPromoActionLeftovers",
                    "participationPercentage", "exceptionProductsCount"):
            number = _integer(row.get(key))
            if number is not None:
                counts[key] = number
        mapped[promo_id] = counts
    return mapped


def _participation(client, promotion_id, store_products=None):
    """Up to 100 participating rows; only ID-matched catalog goods are yours.

    Do not use an ordinary promotion's global price deltas as proof of an
    effect on the selected shop. A row matched to one of the shop's active
    nmIDs is stronger evidence, although the price is still a snapshot.
    """
    goods = _rows(client.get_promotion_participants(promotion_id, limit=100), "nomenclatures")
    products = store_products or {}
    decreases = 0
    neutral = 0
    missing = 0
    matched = []
    raw_samples = []
    for row in goods:
        nm_id = _integer(row.get("nmID"))
        current, planned = _number(row.get("price")), _number(row.get("planPrice"))
        if current is None or planned is None or current == 0:
            missing += 1
        elif planned < current:
            decreases += 1
            if len(raw_samples) < 5:
                raw_samples.append({
                    "nm_id": nm_id, "current": _money(current),
                    "planned": _money(planned),
                })
        else:
            neutral += 1
        if nm_id is None or nm_id not in products:
            continue
        item = products[nm_id]
        matched.append({
            "nm_id": nm_id,
            "title": _bounded_text(item.title or item.vendor_code or f"WB {nm_id}", 110),
            "vendor_code": _bounded_text(item.vendor_code, 50),
            "current": _money(current) if current is not None else None,
            "planned": _money(planned) if planned is not None else None,
            "decrease": bool(current is not None and current > 0
                             and planned is not None and planned < current),
            "current_discount": int(item.current_discount),
            "allowed_discount": int(item.allowed_discount),
        })
    matched.sort(key=lambda row: (not row["decrease"], row["title"].casefold(), row["nm_id"]))
    return {
        "inspected": len(goods),
        "possibly_incomplete": len(goods) == 100,
        "price_decreases": decreases,
        "no_price_decrease": neutral,
        "missing_prices": missing,
        "examples": raw_samples,
        "matched": matched[:100],
        "matched_count": len(matched),
        "matched_decreases": sum(1 for row in matched if row["decrease"]),
    }


def inspect_promotions(client, *, token_type=None, at=None, store_products=None):
    """One bounded read-only audit, max 5 calls for service/personal tokens.

    Never issue detail or product-list requests for a basic/unknown token.
    In auto promotions the WB API does NOT expose item identities, although
    aggregated seller participation counts MAY be available via details.
    """
    now = at or timezone.now()
    start = (now - timedelta(days=1)).astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (now + timedelta(days=30)).astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    promos = _rows(client.get_promotions_calendar(start, end), "promotions")
    results = []
    for promo in promos[:40]:
        promo_id = _integer(promo.get("id", promo.get("promotionID")))
        if not promo_id or any(row["id"] == promo_id for row in results):
            continue
        kind = ("auto" if promo.get("type") == "auto"
                else "regular" if promo.get("type") == "regular" else "unknown")
        results.append({
            "id": promo_id,
            "name": _bounded_text(promo.get("name"), 160) or "Акция WB",
            "type": kind,
            "starts_at": _bounded_text(promo.get("startDateTime"), 32),
            "ends_at": _bounded_text(promo.get("endDateTime"), 32),
            "funding": "unknown",
            "risk": "unknown",
            "explanation": "Финансирование и прибыль по данным календаря неизвестны.",
            "participation": None,
            "seller_counts": None,
        })

    enhanced = token_type in ("personal", "service", "test")
    detail_calls = 0
    inspected_details = 0
    # Calendar + one details call + up to 3 regular-participant calls.
    # Leave room for WB shared-category rate limits.
    if enhanced and results:
        ids = [row["id"] for row in results]
        try:
            details = _promotion_details(client, ids)
        except WBApiError as exc:
            details = {}
            if exc.status == 429:
                enhanced = False
        else:
            detail_calls = 1
        for row in results:
            if row["id"] in details:
                row["seller_counts"] = details[row["id"]]

    if enhanced:
        for row in results:
            if row["type"] != "regular" or inspected_details >= 3:
                continue
            inspected_details += 1
            try:
                p = _participation(client, row["id"], store_products=store_products)
            except WBApiError as exc:
                row["explanation"] = "WB не предоставил состав акции; данных для оценки нет."
                if exc.status == 429:
                    break
                continue
            row["participation"] = p
            if p["matched_decreases"]:
                row["risk"] = "price_drop"
                row["explanation"] = (
                    "Для подтверждённых товаров этого магазина плановая цена ниже "
                    "текущей цены WB. Оценка маржи недоступна."
                )
            elif p["matched_count"] and not p["possibly_incomplete"] and not any(
                x["current"] is None or x["planned"] is None for x in p["matched"]
            ):
                row["risk"] = "no_drop_observed"
                row["explanation"] = (
                    "У сопоставленных товаров снижение плановой цены не выявлено. "
                    "Это не подтверждает выгоду."
                )
            elif store_products is None and p["price_decreases"]:
                # Backward-compatible raw WB sample classification for callers
                # WITHOUT a seller catalog. The production view always supplies
                # the selected store's catalog; it can never use this as proof
                # of that store's item-level risk.
                row["risk"] = "price_drop"
                row["explanation"] = (
                    "В выборке WB есть снижение цены; принадлежность конкретному "
                    "каталогу продавца не проверена."
                )
            elif store_products is None and p["inspected"] and not p["missing_prices"] and not p["possibly_incomplete"]:
                row["risk"] = "no_drop_observed"
                row["explanation"] = (
                    "В выборке WB не обнаружено снижения цены. "
                    "Это не подтверждение прибыли или финансирования."
                )
            elif p["price_decreases"]:
                row["explanation"] = (
                    "В выборке WB обнаружено снижение цены, но не подтверждено "
                    "совпадение товара с текущим каталогом магазина."
                )
    for row in results:
        if row["type"] == "auto":
            row["explanation"] = (
                "Для автоакций WB не предоставляет здесь список артикулов. "
                "Агрегированное количество участников возможно только при доступе к деталям."
            )
    partial = (
        len(promos) >= 40 or
        not enhanced or
        any(row["type"] == "auto" or (
            row["type"] == "regular" and (
                row["participation"] is None or
                row["participation"]["possibly_incomplete"]
            )) for row in results)
    )
    return {
        "promotions": results, "partial": partial,
        "inspection_count": inspected_details,
        "details_requests": detail_calls,
        "fetched_at": now.isoformat(),
        "phase": "full" if enhanced else "calendar",
    }


def _recent_timestamp(iso_text, now, *, hours=24):
    if not isinstance(iso_text, str):
        return False
    try:
        dt = parse_datetime(iso_text)
        if dt is None:
            return False
        if timezone.is_naive(dt):
            dt = dt.replace(tzinfo=dt_timezone.utc)
        return timedelta(0) <= now - dt < timedelta(hours=hours)
    except (TypeError, ValueError, OverflowError):
        return False


def inspect_basic_promotion_step(client, *, cached_promotions, calendar_fetched_at,
                                 store_products=None, at=None):
    """One WB calendar-category request per basic-token refresh, not a burst.

    First read calendar; next eligible request gets seller campaign totals;
    subsequent requests inspect ONE ordinary campaign's product IDs/prices.
    If the calendar is stale, refresh it and preserve only recent evidence for
    unchanged campaign IDs and periods. No price writes or hidden side effects.
    """
    now = at or timezone.now()
    cached = [deepcopy(row) for row in cached_promotions if isinstance(row, dict)]
    if not cached or not calendar_fetched_at or now - calendar_fetched_at >= timedelta(hours=24):
        fresh = inspect_promotions(client, token_type="base", at=now)
        previous = {_integer(row.get("id")): row for row in cached}
        for new in fresh["promotions"]:
            old = previous.get(new["id"])
            if not old or any(new.get(k) != old.get(k) for k in ("type", "starts_at", "ends_at")):
                continue
            for marker, fields in (
                ("details_checked_at", ("seller_counts",)),
                ("participants_checked_at", ("participation", "risk", "explanation")),
            ):
                if _recent_timestamp(old.get(marker), now):
                    new[marker] = old[marker]
                    for field in fields:
                        new[field] = deepcopy(old.get(field))
        fresh["phase"] = "calendar"
        return fresh

    ids = [row["id"] for row in cached if _integer(row.get("id"))]
    if ids and any(not _recent_timestamp(row.get("details_checked_at"), now) for row in cached):
        details = _promotion_details(client, ids)
        for row in cached:
            row["seller_counts"] = details.get(row["id"])
            row["details_checked_at"] = now.isoformat()
        return {
            "promotions": cached, "partial": True, "phase": "details",
            "fetched_at": calendar_fetched_at.isoformat(),
            "inspection_count": 0, "details_requests": 1,
        }

    for row in cached:
        if row.get("type") != "regular" or _recent_timestamp(row.get("participants_checked_at"), now):
            continue
        p = _participation(client, row["id"], store_products=store_products)
        row["participation"] = p
        row["participants_checked_at"] = now.isoformat()
        if p["matched_decreases"]:
            row["risk"] = "price_drop"
            row["explanation"] = "Для товара продавца плановая цена ниже текущей."
        elif p["matched_count"] and not p["possibly_incomplete"] and not any(
            item["current"] is None or item["planned"] is None for item in p["matched"]
        ):
            row["risk"] = "no_drop_observed"
            row["explanation"] = "У сопоставленных товаров снижение плановой цены не выявлено."
        else:
            row["risk"] = "unknown"
            row["explanation"] = "Недостаточно подтверждённых данных по товарам."
        return {
            "promotions": cached, "partial": True, "phase": "participants",
            "fetched_at": calendar_fetched_at.isoformat(),
            "inspection_count": 1, "details_requests": 0,
        }
    # All currently available detail/regular participant samples were read
    # within the freshness period. A calendar refresh is the only safe next call.
    return inspect_basic_promotion_step(
        client, cached_promotions=[], calendar_fetched_at=None,
        store_products=store_products, at=now,
    )
