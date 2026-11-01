"""Read-only, per-request calendar presentation. No network or data writes."""
from datetime import timezone as dt_timezone

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .grid_layout import _balanced_grid_spans

FILTER_LABELS = (
    ("all", "Все"),
    ("active", "Идут"),
    ("upcoming", "Скоро"),
    ("finished", "Завершились"),
    ("price_drop", "Плановая цена ниже"),
    ("participating", "Участвуют"),
    ("unknown", "Нет данных"),
    ("auto", "Автоакции"),
    ("regular", "Обычные"),
)
ALLOWED_FILTERS = frozenset(k for k, _ in FILTER_LABELS)


def _safe_date(raw):
    if not isinstance(raw, str) or len(raw) > 36:
        return None
    try:
        result = parse_datetime(raw)
        if result is None:
            return None
        return result.replace(tzinfo=dt_timezone.utc) if timezone.is_naive(result) else result
    except (TypeError, ValueError, OverflowError):
        return None


def _status(row, now):
    start, end = _safe_date(row.get("starts_at")), _safe_date(row.get("ends_at"))
    if end and end < now:
        return "finished", "Завершилась"
    if start and start > now:
        return "upcoming", "Предстоящая"
    if start and end and start <= now <= end:
        return "active", "Идёт"
    return "unknown_date", "Даты неизвестны"


def build_promotion_presentation(rows, *, selected_filter="all", at=None):
    """Filter real facts; never infer campaign participation from catalog discounts."""
    now = at or timezone.now()
    selected_filter = selected_filter if selected_filter in ALLOWED_FILTERS else "all"
    result = []
    for raw in rows if isinstance(rows, list) else []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item["period"], item["period_label"] = _status(item, now)
        p = item.get("participation") if isinstance(item.get("participation"), dict) else {}
        counts = item.get("seller_counts") if isinstance(item.get("seller_counts"), dict) else {}
        # 'inPromoActionTotal' from /details is seller-specific, not a
        # list of SKUs. 'matched' comes from /nomenclatures inAction=true.
        seller_total = counts.get("inPromoActionTotal")
        seller_total = seller_total if isinstance(seller_total, int) and seller_total >= 0 else None
        matched = p.get("matched") if isinstance(p.get("matched"), list) else []
        item["matched_products"] = matched
        candidates = item.get("candidates") if isinstance(item.get("candidates"), dict) else {}
        item["candidate_products"] = (
            [product for product in candidates.get("matched", []) if isinstance(product, dict)]
            if isinstance(candidates.get("matched"), list) else []
        )
        item["candidate_count"] = len(item["candidate_products"])
        item["candidates_at"] = _safe_date(item.get("candidates_checked_at"))
        item["priced_count"] = sum(
            1 for product in matched
            if isinstance(product, dict)
            and product.get("current") is not None and product.get("planned") is not None
        )
        item["matched_count"] = len(matched)
        item["aggregate_count"] = seller_total
        item["aggregate_known"] = seller_total is not None
        item["participating_stock"] = counts.get("inPromoActionLeftovers")
        item["eligible_stock"] = counts.get("notInPromoActionLeftovers")
        item["excluded_count"] = counts.get("exceptionProductsCount")
        item["participation_percentage"] = counts.get("participationPercentage")
        item["campaign_description"] = counts.get("description") if isinstance(counts.get("description"), str) else ""
        item["campaign_benefits"] = counts.get("advantages") if isinstance(counts.get("advantages"), list) else []
        levels = counts.get("ranging") if isinstance(counts.get("ranging"), list) else []
        labels = {
            "productsInPromotion": "Товары в акции",
            "calculateProducts": "Товары, подходящие для акции",
            "allProducts": "Все товары продавца",
        }
        item["rank_levels"] = [
            {"label": labels[level["condition"]],
             "participation_rate": level["participation_rate"],
             "boost": level["boost"]}
            for level in levels[:8]
            if (isinstance(level, dict)
                and level.get("condition") in labels
                and isinstance(level.get("participation_rate"), int)
                and isinstance(level.get("boost"), int))
        ]
        item["details_at"] = _safe_date(item.get("details_checked_at"))
        item["participants_at"] = _safe_date(item.get("participants_checked_at"))
        item["has_price_drop"] = any(
            isinstance(good, dict) and good.get("decrease") is True for good in matched
        )
        item["has_participation"] = (
            (seller_total is not None and seller_total > 0) or bool(matched)
        )
        item["has_evidence"] = seller_total is not None or bool(matched)
        item["is_unverified"] = not item["has_evidence"]
        item["is_priority"] = item["has_participation"] or item["candidate_count"] > 0
        item["display_type"] = (
            "Автоакция" if item.get("type") == "auto"
            else "Обычная" if item.get("type") == "regular" else "Акция WB"
        )
        result.append(item)
    def matches(item, key):
        if key == "all":
            return True
        if key in ("active", "upcoming", "finished"):
            return item["period"] == key
        if key == "price_drop":
            return item["has_price_drop"]
        if key == "participating":
            return item["has_participation"]
        if key == "unknown":
            return item["is_unverified"]
        if key in ("auto", "regular"):
            return item.get("type") == key
        return False

    counts = {key: sum(matches(item, key) for item in result) for key, _ in FILTER_LABELS}
    chips = [
        {"key": key, "label": label, "count": counts[key]}
        for key, label in FILTER_LABELS
        if key == "all" or counts[key] > 0 or key == selected_filter
    ]
    visible = [item for item in result if matches(item, selected_filter)]
    for item, span in zip(visible, _balanced_grid_spans(len(visible))):
        item["grid_span"] = span
    # Rank campaigns with actual shop participation or opt-in eligible SKUs
    # above unselected WB promotions; don't confuse eligible with joined.
    focus = [item for item in visible if item["is_priority"]]
    focus.sort(key=lambda item: (
        not item["has_price_drop"],
        not item["has_participation"],
        -(item["aggregate_count"] or 0),
        item.get("starts_at") or "",
    ))
    for item, span in zip(focus, _balanced_grid_spans(len(focus))):
        item["grid_span"] = span
    other = [item for item in visible if not item["is_priority"]]
    other.sort(key=lambda item: (
        0 if item["period"] == "active" else 1 if item["period"] == "upcoming" else 2,
        item.get("starts_at") or "",
    ))
    participation_events = sum(
        item["aggregate_count"] for item in result
        if isinstance(item["aggregate_count"], int) and item["aggregate_count"] > 0
    )
    all_matched = [
        product for item in result for product in item["matched_products"]
        if isinstance(product, dict)
    ]
    return {
        "promotion_cards": visible,
        "promotion_focus": focus,
        "promotion_other": other,
        "promotion_active_count": counts["active"],
        "promotion_participating_count": counts["participating"],
        "promotion_participation_events": participation_events,
        "promotion_priced_count": sum(item["priced_count"] for item in result),
        "promotion_price_decreases": sum(item["has_price_drop"] for item in result),
        "promotion_candidate_total": sum(item["candidate_count"] for item in result),
        "promotion_chips": chips,
        "promotion_filter": selected_filter,
        "promotion_counts": counts,
        "promotion_total": len(result),
    }
