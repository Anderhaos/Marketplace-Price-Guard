from django.conf import settings
from django.utils import timezone
from django.db.models import Case, IntegerField, Value, When
from .models import AccessGrant


# Inspired by BeClients billing-capabilities.ts: one effective AccessGrant
# selects a small set of server-enforced features. This is intentionally
# not a second subscription/payment ledger.
PLAN_CAPABILITIES = {
    "standard": frozenset({"discount_guard", "notifications", "web_assistant_basic"}),
    "smart": frozenset({
        "discount_guard", "notifications", "web_assistant_basic",
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


def first_inventory_import_permitted(account):
    """One safe first read before the trial starts; no expired-trial reset.

    Any prior subscription or trial requires the normal active-access gate.
    Never allow pending price writes to exploit onboarding access.
    """
    if not (settings.SAAS_PIN_AUTH_ENABLED and settings.SAAS_ACCESS_ENFORCED
            and settings.SAAS_WB_NETWORK_ENABLED):
        return False
    if (account.initial_sync_completed_at is not None or not account.check_requested
            or account.enabled or account.apply_all_requested or account.apply_requested_ids):
        return False
    if AccessGrant.objects.filter(user_id=account.user_id).exists():
        return False
    from guard.models import Upload
    return not Upload.objects.filter(account_id=account.pk, status="pending").exists()


def has_smart_access(user_id, at=None):
    """Compatibility alias: privileged capability always checked server-side."""
    return has_plan_capability(user_id, "store_advice", at=at)
