from .models import GuardEvent
from push.models import PushMessage
from billing.access import capabilities_for_user


IMPORTANT_EVENT_KINDS = ("confirmed", "upload_error", "error")


def notification_count(request):
    if not request.user.is_authenticated:
        return {"unread_notification_count": 0, "seller_capabilities": frozenset()}
    capabilities = capabilities_for_user(request.user.pk)
    count = GuardEvent.objects.filter(
        account__user=request.user,
        kind__in=IMPORTANT_EVENT_KINDS,
        read_at__isnull=True,
    ).count()
    digests = PushMessage.objects.filter(user=request.user, kind__in=("digest", "smart"), read_at__isnull=True).count()
    return {"unread_notification_count": count + digests, "seller_capabilities": capabilities}
