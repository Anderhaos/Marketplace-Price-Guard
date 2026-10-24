"""Read-only WB MCP tools, backed exclusively by previously verified NoSale DB facts.

No API token decryption, WB network, uploads or seller-price mutations here.
"""
from django.utils import timezone
from guard.advisor import store_recommendations
from guard.models import GuardEvent, Product, PromotionSnapshot


def _iso(value):
    return value.isoformat() if value is not None else None


def _wb(account):
    if account.marketplace != "wb":
        raise PermissionError("Selected store is not a Wildberries store")


def overview(account):
    _wb(account)
    advice = store_recommendations(account)
    return {
        "marketplace": "wb",
        "store_id": account.pk,
        "store_name": account.name[:90],
        "read_only": True,
        "source": "NoSale saved observations (no real-time WB API request)",
        "last_check_at": _iso(account.last_check_at),
        "last_observed_at": _iso(advice.get("last_observed_at")),
        "data_stale": bool(advice.get("is_stale", True)),
        "metrics": advice["metrics"],
        "warnings": [
            {"title": row["title"][:110], "detail": row["detail"][:300]}
            for row in advice["recommendations"][:6]
        ],
        "profit_or_funding_verified": False,
    }


def products(account, *, limit=20, offset=0):
    _wb(account)
    limit = max(1, min(50, int(limit)))
    offset = max(0, min(1000, int(offset)))
    qs = Product.objects.filter(account=account, active=True).order_by("nm_id")
    total = qs.count()
    rows = []
    for item in qs[offset:offset+limit]:
        rows.append({
            "nm_id": item.nm_id,
            "title": item.title[:160],
            "current_discount_pct": item.current_discount,
            "allowed_discount_pct": item.allowed_discount,
            "over_limit": item.current_discount > item.allowed_discount,
            "price": str(item.price) if item.price is not None else None,
            "discounted_price": str(item.discounted_price) if item.discounted_price is not None else None,
            "currency": item.currency,
            "observed_at": _iso(item.checked_at),
        })
    return {
        "marketplace": "wb", "store_id": account.pk, "total": total,
        "limit": limit, "offset": offset, "items": rows,
        "truncated": total > offset+len(rows),
        "source": "NoSale cached product observations; not live WB",
    }


def events(account, *, limit=20):
    _wb(account)
    limit = max(1, min(50, int(limit)))
    qs = GuardEvent.objects.filter(account=account).order_by("-created_at", "-pk")
    # Never return free-form event text/details: they can contain untrusted or sensitive data.
    return {"marketplace": "wb", "store_id": account.pk,
            "events": [
                {"kind": x.kind[:32], "nm_id": x.nm_id, "observed_at": _iso(x.created_at)}
                for x in qs[:limit]
            ],
            "source": "NoSale audit event metadata (raw messages excluded)"}


def promotions(account, *, limit=15):
    _wb(account)
    limit = max(1, min(30, int(limit)))
    snapshot = PromotionSnapshot.objects.filter(account=account).first()
    if snapshot is None:
        return {"marketplace": "wb", "store_id": account.pk, "fetched_at": None,
                "partial": True, "items": [],
                "message": "No saved promotion calendar; seller must request a guarded UI refresh."}
    # The saved audit is already normalized; return only proven/safe subsets.
    items = []
    for x in (snapshot.promotions or [])[:limit]:
        if not isinstance(x, dict):
            continue
        p = x.get("participation") if isinstance(x.get("participation"), dict) else {}
        items.append({
            "id": x.get("id") if type(x.get("id")) is int else None,
            "name": str(x.get("name") or "")[:160],
            "type": x.get("type") if x.get("type") in ("auto", "regular") else "unknown",
            "risk": x.get("risk") if x.get("risk") in ("price_drop", "no_drop_observed") else "unknown",
            "funding": "unknown",
            "inspected": p.get("inspected") if type(p.get("inspected")) is int else None,
            "price_decreases": p.get("price_decreases") if type(p.get("price_decreases")) is int else None,
            "incomplete_items": bool(p.get("possibly_incomplete", True)),
        })
    return {
        "marketplace": "wb", "store_id": account.pk, "fetched_at": _iso(snapshot.fetched_at),
        "partial": snapshot.partial or len(snapshot.promotions or []) > limit,
        "items": items,
        "profit_or_funding_verified": False,
        "source": "Read-only cached WB promotion audit; finance not verified",
    }
