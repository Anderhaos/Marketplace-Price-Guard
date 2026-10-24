from django.conf import settings
from django.utils import timezone
from django.db.models import Case, IntegerField, Value, When
from .models import AccessGrant


# Inspired by BeClients billing-capabilities.ts: one effective AccessGrant
# selects a small set of server-enforced features. This is intentionally
# not a second subscription/payment ledger.
PLAN_CAPABILITIES = {
    "standard": frozenset({"discount_guard", "telegram_status", "telegram_alerts"}),
    "smart": frozenset({
        "discount_guard", "telegram_status", "telegram_alerts",
        "store_advice", "ai_advice", "ai_questions",
    }),
}


def effective_plan(user_id, at=None):
    """No active grant means no paid capabilities; Smart wins overlapping grants."""
    now = at or timezone.now()
    return AccessGrant.objects.filter(
        user_id=user_id, starts_at__lte=now, ends_at__gt=now, revoked_at__isnull=True,
    ).order_by(
        Case(When(plan="smart", then=Value(0)), default=Value(1), output_field=IntegerField()),
        "-ends_at",
    ).values_list("plan", flat=True).first()


def has_plan_capability(user_id, capability, at=None):
    if not isinstance(capability, str) or not capability:
        return False
    plan = effective_plan(user_id, at=at)
    return capability in PLAN_CAPABILITIES.get(plan, frozenset())


def has_active_access(user_id, at=None):
    if not settings.SAAS_ACCESS_ENFORCED:
        return True
    now = at or timezone.now()
    return AccessGrant.objects.filter(
        user_id=user_id, starts_at__lte=now, ends_at__gt=now,
        revoked_at__isnull=True,
    ).exists()

def guard_is_permitted(account):
    return settings.SAAS_WB_NETWORK_ENABLED and has_active_access(account.user_id)


def has_smart_access(user_id, at=None):
    """Compatibility alias: privileged capability always checked server-side."""
    return has_plan_capability(user_id, "store_advice", at=at)
