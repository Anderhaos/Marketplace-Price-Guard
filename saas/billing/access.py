from django.conf import settings
from django.utils import timezone
from .models import AccessGrant

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
    """Premium entitlement is not implied by legacy grants or dev access bypass."""
    now = at or timezone.now()
    return AccessGrant.objects.filter(
        user_id=user_id, plan="smart", starts_at__lte=now, ends_at__gt=now,
        revoked_at__isnull=True,
    ).exists()
