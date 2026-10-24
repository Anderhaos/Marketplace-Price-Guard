"""NoSale read-only WB promotion audit.

The official calendar cannot list auto-promotion goods. No finance data is
available here, so never claim profit, WB funding or safe automatic exit.
The only implemented WB operations in this module are GET requests.
"""
from datetime import timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.utils import timezone

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


def _bounded_text(value, limit):
    return str(value or "")[:limit]


def _participation(client, promotion_id):
    goods = _rows(client.get_promotion_participants(promotion_id, limit=100), "nomenclatures")
    decrease = 0
    unchanged_or_higher = 0
    unknown = 0
    samples = []
    for row in goods:
        current, planned = _number(row.get("price")), _number(row.get("planPrice"))
        if current is None or planned is None or current == 0:
            unknown += 1
            continue
        if planned < current:
            decrease += 1
            if len(samples) < 5:
                nm_id = row.get("nmID")
                samples.append({
                    "nm_id": int(nm_id) if str(nm_id).isdigit() else None,
                    "current": str(current), "planned": str(planned),
                })
        else:
            unchanged_or_higher += 1
    return {
        "inspected": len(goods), "possibly_incomplete": len(goods) == 100,
        "price_decreases": decrease, "no_price_decrease": unchanged_or_higher,
        "missing_prices": unknown, "examples": samples,
    }


def inspect_promotions(client, *, token_type=None, at=None):
    """One bounded audit pass. Basic WB token: ONLY one calendar request/hour.

    Personal/service/test classes may read at most three ordinary promotion
    participant pages in addition to the one calendar page. No writes.
    """
    now = at or timezone.now()
    start = (now - timedelta(days=1)).astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (now + timedelta(days=30)).astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    promos = _rows(client.get_promotions_calendar(start, end), "promotions")
    results = []
    max_details = 3 if token_type in ("personal", "service", "test") else 0
    inspected_details = 0
    for promo in promos[:40]:
        promo_id = promo.get("id", promo.get("promotionID"))
        if not str(promo_id).isdigit():
            continue
        kind = "auto" if promo.get("type") == "auto" else (
            "regular" if promo.get("type") == "regular" else "unknown"
        )
        detail = {
            "id": int(promo_id),
            "name": _bounded_text(promo.get("name"), 160) or "Акция WB",
            "type": kind,
            "starts_at": _bounded_text(promo.get("startDateTime"), 32),
            "ends_at": _bounded_text(promo.get("endDateTime"), 32),
            "funding": "unknown",
            "risk": "unknown",
            "explanation": "Источник финансирования и маржа не подтверждены.",
            "participation": None,
        }
        # Auto-promo item composition is explicitly not provided by WB API.
        if kind == "auto":
            detail["explanation"] = (
                "Автоакция: WB не раскрывает состав товаров этим API. "
                "Проверяйте цены и ограничения в кабинете продавца."
            )
        elif kind == "regular" and inspected_details < max_details:
            inspected_details += 1
            try:
                summary = _participation(client, detail["id"])
            except WBApiError as exc:
                # If the shared calendar quota is exhausted, stop probing;
                # the list remains usable, but must be flagged as incomplete.
                if exc.status == 429:
                    detail["explanation"] = "Календарь WB ограничил частоту запросов; состав акции не получен."
                    results.append(detail)
                    max_details = 0
                    continue
                detail["explanation"] = "Состав акции недоступен через WB API; риск не определён."
            else:
                detail["participation"] = summary
                if summary["price_decreases"]:
                    detail["risk"] = "price_drop"
                    detail["explanation"] = (
                        "У части проверенных товаров плановая цена ниже текущей. "
                        "Проверьте экономику и условия акции до участия."
                    )
                elif summary["inspected"] and not summary["missing_prices"] and not summary["possibly_incomplete"]:
                    detail["risk"] = "no_drop_observed"
                    detail["explanation"] = (
                        "В проверенном списке не обнаружено снижения плановой цены. "
                        "Это не подтверждение прибыли и не доказательство скидки за счёт WB."
                    )
                else:
                    detail["explanation"] = "Данных недостаточно для оценки изменения цены."
        results.append(detail)
    # Full page or bounded detail sampling means some campaign/item risks unknown.
    skipped_regular = sum(1 for row in results if row["type"] == "regular" and row["participation"] is None)
    partial = len(promos) >= 40 or skipped_regular > 0 or any(row["type"] == "auto" for row in results) or any(
        row["participation"] and row["participation"]["possibly_incomplete"] for row in results
    )
    return {"promotions": results, "partial": partial,
            "inspection_count": inspected_details, "fetched_at": now.isoformat()}
