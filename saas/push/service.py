"""Server-side Web Push registration, queuing and delivery.

No synchronous HTTP calls to push providers from user requests or WB checks.
"""
import base64
import json
import re
from datetime import datetime, time, timedelta, timezone as dt_timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from guard.models import GuardAccount, GuardEvent, Product
from guard.advisor import store_recommendations
from billing.access import has_smart_access
from billing.models import OwnerAISettings, OwnerOperationsSettings
from .models import NotificationPreference, PushDelivery, PushMessage, PushSubscription

IMPORTANT_KINDS = ("confirmed", "error", "upload_error")
ALLOWED_HOSTS = frozenset({
    "fcm.googleapis.com", "updates.push.services.mozilla.com",
    "web.push.apple.com", "webpush.push.apple.com",
})
URL = "/notifications/"
SUPPORTED_TIMEZONES = frozenset({
    "Europe/Moscow", "Europe/Berlin", "Asia/Yekaterinburg",
    "Asia/Almaty", "Asia/Novosibirsk", "Asia/Vladivostok",
})


def validate_endpoint(endpoint):
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        raise ValueError("Некорректный адрес Push-сервера.")
    try:
        parsed = urlsplit(endpoint)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        raise ValueError("Некорректный адрес Push-сервера.")
    allowed = hostname in ALLOWED_HOSTS or hostname.endswith(".push.apple.com") or hostname.endswith(".notify.windows.com")
    if (parsed.scheme != "https" or not allowed or parsed.username or parsed.password
            or parsed.fragment or port not in (None, 443)):
        raise ValueError("Этот Push-провайдер пока не поддерживается.")
    return endpoint


def validate_keys(keys):
    if not isinstance(keys, dict):
        raise ValueError("Не переданы ключи подписки.")
    result = {}
    for name, expected_size in (("p256dh", 65), ("auth", 16)):
        value = keys.get(name)
        if not isinstance(value, str) or len(value) > 256 or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
            raise ValueError("Неверный формат ключей Web Push.")
        try:
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        except (ValueError, base64.binascii.Error):
            raise ValueError("Неверный формат ключей Web Push.")
        if len(raw) != expected_size:
            raise ValueError("Неверная длина ключей Web Push.")
        result[name] = value
    return result


def register_subscription(user, subscription):
    if not isinstance(subscription, dict):
        raise ValueError("Подписка должна быть объектом.")
    endpoint = validate_endpoint(subscription.get("endpoint"))
    keys = validate_keys(subscription.get("keys"))
    with transaction.atomic():
        existing = PushSubscription.objects.select_for_update().filter(endpoint=endpoint).first()
        if existing:
            if existing.user_id != user.pk:
                # Shared browser switched accounts: never deliver queued old-user messages.
                existing.deliveries.exclude(message__user=user).update(state="gone")
            existing.user = user
            existing.p256dh = keys["p256dh"]
            existing.auth = keys["auth"]
            existing.active = True
            existing.save(update_fields=["user", "p256dh", "auth", "active", "updated_at"])
            return existing
        return PushSubscription.objects.create(
            user=user, endpoint=endpoint, p256dh=keys["p256dh"], auth=keys["auth"],
        )


def unsubscribe(user, endpoint):
    if not isinstance(endpoint, str):
        return
    subscriptions = PushSubscription.objects.filter(user=user, endpoint=endpoint)
    PushDelivery.objects.filter(subscription__in=subscriptions, state__in=["pending", "sending"]).update(state="gone")
    subscriptions.update(active=False)


def _enqueue_for_devices(message, not_before=None):
    query = PushSubscription.objects.filter(user_id=message.user_id, active=True)
    if not_before:
        query = query.filter(created_at__lte=not_before)
    PushDelivery.objects.bulk_create([
        PushDelivery(message=message, subscription=subscription)
        for subscription in query.order_by("-updated_at")[:10]
    ], ignore_conflicts=True)


def queue_new_events(now=None):
    now = now or timezone.now()
    policy = OwnerOperationsSettings.runtime()
    if policy is None:
        return 0
    instant_enabled = policy.instant_events_enabled
    cutoff = now - timedelta(hours=48)
    events = (
        GuardEvent.objects.filter(kind__in=IMPORTANT_KINDS, created_at__gte=cutoff)
        .exclude(push_messages__isnull=False)
        .select_related("account", "account__user")
        .order_by("id")[:200]
    )
    queued = 0
    for event in events:
        title = {
            "confirmed": "WB подтвердил исправление",
            "error": "Ошибка проверки WB",
            "upload_error": "WB не подтвердил исправление",
        }[event.kind]
        items = event.details.get("changes", [])
        if event.kind == "confirmed" and items:
            body = f"{event.account.name}: исправлено товаров — {len(items)}."
        else:
            body = f"{event.account.name}: {event.message[:240]}"
        pref, _ = NotificationPreference.objects.get_or_create(user_id=event.account.user_id)
        message, created = PushMessage.objects.get_or_create(
            event=event, defaults={
                "user_id": event.account.user_id, "kind": "event",
                "title": title, "body": body[:400], "url": URL,
            },
        )
        if created:
            if instant_enabled and pref.instant_enabled:
                # Owner may disable mid-cycle; do not create new push deliveries.
                live_policy = OwnerOperationsSettings.runtime()
                if live_policy is not None and live_policy.instant_events_enabled:
                    _enqueue_for_devices(message, not_before=event.created_at)
            queued += 1
    return queued



def _highest_priority_smart_advice(user, *, now):
    """One factual action across ALL of this user's connected WB stores.

    No WB polling, model calls or cross-user data. This runs once per user's
    local day, after the existing outbox deduplication check.
    """
    choice = None
    connected_count = 0
    stores = (
        GuardAccount.objects.filter(user=user, marketplace="wb")
        .exclude(token_ciphertext="")
        .order_by("id")
    )
    for store in stores.iterator(chunk_size=20):
        connected_count += 1
        advisory = store_recommendations(store, now=now)
        finding = next(
            (item for item in advisory["recommendations"] if item["priority"] <= 2),
            None,
        )
        if finding is None:
            continue
        if choice is None or finding["priority"] < choice[1]["priority"]:
            choice = (store, finding)
    return choice, connected_count


def create_daily_digests(now=None):
    now = now or timezone.now()
    policy = OwnerOperationsSettings.runtime()
    if policy is None or not policy.daily_digests_enabled:
        return 0
    count = 0
    User = get_user_model()
    sellers = User.objects.filter(is_active=True)
    if settings.SAAS_ACCESS_ENFORCED:
        # Filter with the canonical AccessGrant ledger *in the database*.
        # Avoid one entitlement query per registered user every 30 seconds;
        # distinct prevents duplicate rows for overlapping paid/trial grants.
        sellers = sellers.filter(
            saas_access_grants__starts_at__lte=now,
            saas_access_grants__ends_at__gt=now,
            saas_access_grants__revoked_at__isnull=True,
        ).distinct()
    for user in sellers.iterator(chunk_size=100):
        live_policy = OwnerOperationsSettings.runtime()
        if live_policy is None or not live_policy.daily_digests_enabled:
            break
        pref, _ = NotificationPreference.objects.get_or_create(user=user)
        if not pref.daily_enabled:
            continue
        tzname = pref.timezone_name if pref.timezone_name in SUPPORTED_TIMEZONES else "Europe/Moscow"
        tz = ZoneInfo(tzname)
        local_now = timezone.localtime(now, tz)
        if local_now.hour < pref.daily_hour:
            continue
        day = local_now.date()
        # This cycle runs every 30 seconds: never recompute a delivered daily
        # digest or scan a seller's product catalog over and over.
        if PushMessage.objects.filter(user=user, digest_date=day).exists():
            continue
        start = datetime.combine(day, time.min, tzinfo=tz).astimezone(dt_timezone.utc)
        checks = GuardEvent.objects.filter(account__user=user, kind="check", created_at__gte=start, created_at__lte=now).count()
        confirmed = GuardEvent.objects.filter(
            account__user=user, kind="confirmed", created_at__gte=start, created_at__lte=now,
        )
        corrected = sum(len(e.details.get("changes", [])) or 1 for e in confirmed)
        errors = GuardEvent.objects.filter(
            account__user=user, kind__in=("error", "upload_error"),
            created_at__gte=start, created_at__lte=now,
        ).count()
        active_products = Product.objects.filter(account__user=user, active=True).count()
        title = f"NoSale — отчёт за {day:%d.%m}"
        body = f"Товаров под контролем: {active_products}; проверок: {checks}; исправлений: {corrected}; ошибок: {errors}."
        link = URL
        # The regular daily status is tariff-neutral. Separate Smart briefs
        # have their own change-triggered, strictly capped 08:00/20:00 windows.
        message, created = PushMessage.objects.get_or_create(
            user=user, digest_date=day,
            defaults={"kind": "digest", "title": title, "body": body[:400], "url": link},
        )
        if created:
            _enqueue_for_devices(message)
            count += 1
    return count


SMART_CHANGE_KINDS = (
    "price_changed", "discount_changed", "rule",
    "confirmed", "error", "upload_error",
)


def create_smart_briefs(now=None):
    """At most two change-triggered Smart briefs per local day.

    No WB requests or AI costs. Each 08:00/20:00 local window is unique in
    PostgreSQL; late workers do not replay stale windows. Ordinary check
    heartbeat entries cannot trigger a brief.
    """
    now = now or timezone.now()
    policy = OwnerOperationsSettings.runtime()
    ai = OwnerAISettings.objects.filter(pk=1).first()
    if policy is None or not policy.daily_digests_enabled or ai is None:
        return 0
    limit = min(2, max(0, ai.smart_briefs_per_day))
    if not limit:
        return 0

    sellers = get_user_model().objects.filter(
        is_active=True, saas_access_grants__plan="smart",
        saas_access_grants__starts_at__lte=now,
        saas_access_grants__ends_at__gt=now,
        saas_access_grants__revoked_at__isnull=True,
    ).distinct()
    queued = 0
    for user in sellers.iterator(chunk_size=100):
        live_policy = OwnerOperationsSettings.runtime()
        live_ai = OwnerAISettings.objects.filter(pk=1).first()
        if live_policy is None or not live_policy.daily_digests_enabled or live_ai is None:
            break
        current_limit = min(2, max(0, live_ai.smart_briefs_per_day))
        if current_limit == 0:
            break
        pref, _ = NotificationPreference.objects.get_or_create(user=user)
        if not pref.daily_enabled or not has_smart_access(user.pk, at=now):
            continue
        tzname = pref.timezone_name if pref.timezone_name in SUPPORTED_TIMEZONES else "Europe/Moscow"
        tz = ZoneInfo(tzname)
        local_now = timezone.localtime(now, tz)
        hour = 20 if local_now.hour >= 20 else (8 if local_now.hour >= 8 and current_limit >= 2 else None)
        if hour is None:
            continue
        slot_end = datetime.combine(local_now.date(), time(hour=hour), tzinfo=tz).astimezone(dt_timezone.utc)
        if now >= slot_end + timedelta(hours=2):
            continue
        slot_start = (datetime.combine(local_now.date(), time(hour=8 if hour == 20 else 20), tzinfo=tz)
                      - (timedelta(days=1) if hour == 8 else timedelta())).astimezone(dt_timezone.utc)
        slot = f"{local_now.date().isoformat()}:{hour:02d}"
        if PushMessage.objects.filter(user=user, smart_slot=slot).exists():
            continue
        events = GuardEvent.objects.filter(
            account__user=user, kind__in=SMART_CHANGE_KINDS,
            created_at__gte=slot_start, created_at__lt=slot_end,
        )
        counts = {row["kind"]: row["n"] for row in events.values("kind").annotate(n=Count("id"))}
        if not counts:
            continue
        changes = counts.get("price_changed", 0) + counts.get("discount_changed", 0)
        rules = counts.get("rule", 0)
        confirmed = counts.get("confirmed", 0)
        errors = counts.get("error", 0) + counts.get("upload_error", 0)
        parts = []
        if changes:
            parts.append(f"изменений цен/скидок — {changes}")
        if rules:
            parts.append(f"правил NoSale — {rules}")
        if confirmed:
            parts.append(f"подтверждений исправлений WB — {confirmed}")
        if errors:
            parts.append(f"ошибок — {errors}")
        body = "За 12 часов: " + "; ".join(parts) + "."
        selected, store_count = _highest_priority_smart_advice(user, now=now)
        link = URL
        if selected is not None:
            store, advice = selected
            body += f" Проверьте: {store.name[:60]} — {advice['title']}."
            link = "/stores/" if store_count > 1 else "/advisor/"
        message, created = PushMessage.objects.get_or_create(
            user=user, smart_slot=slot,
            defaults={
                "kind": "smart", "title": f"NoSale Smart — изменения {local_now:%d.%m}, {hour:02d}:00",
                "body": body[:400], "url": link,
            },
        )
        if created:
            _enqueue_for_devices(message)
            queued += 1
    return queued


def deliver_due_pushes(now=None, send=None):
    """Deliver independently; owner pause retains pending items for later."""
    policy = OwnerOperationsSettings.runtime()
    if policy is None or not policy.push_delivery_enabled:
        return 0
    if not (settings.PUSH_VAPID_PRIVATE_KEY and settings.PUSH_VAPID_PUBLIC_KEY):
        return 0
    if send is None:
        from pywebpush import webpush as send
    now = now or timezone.now()
    expired_lease = now - timedelta(minutes=5)
    due = PushDelivery.objects.filter(
        Q(state="pending", next_attempt_at__lte=now) |
        Q(state="sending", locked_at__lt=expired_lease)
    ).select_related("message", "subscription").order_by("next_attempt_at", "id")[:max(1, min(20, policy.push_batch_size))]
    processed = 0
    for delivery in due:
        live_policy = OwnerOperationsSettings.runtime()
        if live_policy is None or not live_policy.push_delivery_enabled:
            break
        claim = PushDelivery.objects.filter(pk=delivery.pk).filter(
            Q(state="pending", next_attempt_at__lte=now) |
            Q(state="sending", locked_at__lt=expired_lease)
        ).update(state="sending", attempts=F("attempts") + 1, locked_at=now)
        if not claim:
            continue
        delivery.refresh_from_db(fields=["attempts"])
        subscription = delivery.subscription
        if not subscription.active or subscription.user_id != delivery.message.user_id:
            PushDelivery.objects.filter(pk=delivery.pk).update(state="gone")
            continue
        try:
            validate_endpoint(subscription.endpoint)  # Prevent unexpected outbound destinations.
            send(
                subscription_info={"endpoint": subscription.endpoint, "keys": {
                    "p256dh": subscription.p256dh, "auth": subscription.auth,
                }},
                data=json.dumps({
                    "title": delivery.message.title, "body": delivery.message.body,
                    "url": delivery.message.url if delivery.message.url in (URL, "/advisor/", "/stores/") else URL,
                }, ensure_ascii=False),
                vapid_private_key=settings.PUSH_VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.PUSH_VAPID_SUBJECT},
                ttl=1800,
                timeout=6,
            )
        except Exception as error:
            code = getattr(error, "status_code", None)
            if code in (404, 410):
                unsubscribe(subscription.user, subscription.endpoint)
                PushDelivery.objects.filter(pk=delivery.pk).update(state="gone")
            else:
                terminal = delivery.attempts >= 5
                PushDelivery.objects.filter(pk=delivery.pk).update(
                    state="failed" if terminal else "pending",
                    next_attempt_at=now + timedelta(minutes=min(60, 2 ** delivery.attempts)),
                    locked_at=None,
                )
        else:
            PushDelivery.objects.filter(pk=delivery.pk).update(
                state="sent", sent_at=timezone.now(), locked_at=None,
            )
        processed += 1
    return processed


def process_notification_cycle(now=None, send=None):
    now = now or timezone.now()
    queued = queue_new_events(now=now)
    digests = create_daily_digests(now=now)
    briefs = create_smart_briefs(now=now)
    sent = deliver_due_pushes(now=now, send=send)
    return {"events": queued, "digests": digests, "smart_briefs": briefs, "attempts": sent}
