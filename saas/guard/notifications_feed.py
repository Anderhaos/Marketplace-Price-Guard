"""Compact, tenant-scoped inbox with grouped technical incidents.

Raw GuardEvent rows remain intact for auditing. The notification center
aggregates repeated technical errors instead of emitting one UI card per poll.
"""
from django.core.paginator import Paginator
from django.db.models import Count, Max, Min, Q

from push.models import PushMessage
from .models import GuardEvent

IMPORTANT_EVENT_KINDS = ("confirmed", "upload_error", "error")
INBOX_TYPES = ("all", "errors", "changes", "reports")
MAX_GROUPS = 250
MAX_REPORTS = 80


def unread_notification_groups(user):
    """Badge represents unread conversations, not repeated error rows."""
    technical = (
        GuardEvent.objects.filter(
            account__user=user,
            kind__in=IMPORTANT_EVENT_KINDS,
            read_at__isnull=True,
        )
        .values("account_id", "kind", "message")
        .distinct()
        .count()
    )
    reports = PushMessage.objects.filter(
        user=user, kind__in=("digest", "smart"), read_at__isnull=True
    ).count()
    return technical + reports


def notification_page(user, *, category="all", page="1"):
    if category not in INBOX_TYPES:
        category = "all"
    groups = list(
        GuardEvent.objects.filter(
            account__user=user, kind__in=IMPORTANT_EVENT_KINDS,
        )
        .values("account_id", "kind", "message")
        .annotate(
            occurrences=Count("pk"),
            unread_count=Count("pk", filter=Q(read_at__isnull=True)),
            first_at=Min("created_at"),
            last_at=Max("created_at"),
            latest_id=Max("pk"),
        )
        .order_by("-last_at", "-latest_id")[:MAX_GROUPS]
    )
    latest = {
        e.pk: e for e in GuardEvent.objects.select_related("account").filter(
            pk__in=[group["latest_id"] for group in groups],
            account__user=user,
        )
    }
    items = []
    for group in groups:
        event = latest.get(group["latest_id"])
        if event is None:
            continue
        is_error = event.kind in ("error", "upload_error")
        is_rate_limit = event.kind == "error" and (
            "HTTP 429" in event.message or "429" in event.message
        )
        if is_rate_limit:
            title = "Ограничение запросов Wildberries"
            body = ("WB временно ограничивал обращения к API. "
                    "NoSale соблюдает паузы между запросами.")
        elif event.kind == "confirmed":
            title = "Wildberries подтвердил исправление скидок"
            body = event.message
        elif event.kind == "upload_error":
            title = "Не удалось применить скидки"
            body = event.message
        else:
            title = "Ошибка проверки Wildberries"
            body = event.message
        resolved = bool(
            is_error
            and event.account.last_check_at
            and event.account.last_check_at > event.created_at
            and not event.account.last_error
        )
        items.append({
            "source": "event", "category": "errors" if is_error else "changes",
            "title": title, "body": body, "store": event.account.name,
            "created_at": group["last_at"], "first_at": group["first_at"],
            "occurrences": group["occurrences"], "unread": bool(group["unread_count"]),
            "resolved": resolved, "is_error": is_error, "is_success": not is_error,
            "changes": (event.details or {}).get("changes") or [],
            "key": event.pk,
        })

    reports = list(PushMessage.objects.filter(
        user=user, kind__in=("digest", "smart"),
    ).order_by("-created_at", "-pk")[:MAX_REPORTS])
    for report in reports:
        items.append({
            "source": "report", "category": "reports",
            "title": report.title, "body": report.body,
            "created_at": report.created_at, "first_at": report.created_at,
            "occurrences": 1, "unread": report.read_at is None,
            "resolved": False, "is_error": False, "is_success": False,
            "changes": [], "store": "", "key": report.pk,
        })
    counts = {"all": len(items), "errors": 0, "changes": 0, "reports": 0}
    for item in items:
        counts[item["category"]] += 1
    filtered = sorted(
        (item for item in items if category == "all" or item["category"] == category),
        key=lambda item: (item["created_at"], item["key"]),
        reverse=True,
    )
    return {
        "notification_items": Paginator(filtered, 12).get_page(page),
        "notification_filter": category,
        "notification_counts": counts,
    }
