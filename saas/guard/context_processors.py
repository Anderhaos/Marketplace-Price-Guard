from django.conf import settings
from .models import GuardAccount, GuardEvent, PromotionSnapshot, FinanceSnapshot
from .stores import SESSION_KEY
from push.models import PushMessage
from billing.access import capabilities_for_user
from billing.trial import access_snapshot


IMPORTANT_EVENT_KINDS = ("confirmed", "upload_error", "error")


def notification_count(request):
    if not request.user.is_authenticated:
        return {
            "unread_notification_count": 0,
            "seller_capabilities": frozenset(),
            "seller_has_other_stores": False,
            "seller_has_promotion_data": False,
            "seller_finance_available": False,
            "seller_header_access": None,
        }
    capabilities = capabilities_for_user(request.user.pk)
    count = GuardEvent.objects.filter(
        account__user=request.user,
        kind__in=IMPORTANT_EVENT_KINDS,
        read_at__isnull=True,
    ).count()
    digests = PushMessage.objects.filter(
        user=request.user, kind__in=("digest", "smart"), read_at__isnull=True
    ).count()

    # Use the seller's OWN selected store. Never leak another account's
    # campaigns or show empty / operator-disabled feature screens.
    store_ids = list(GuardAccount.objects.filter(user=request.user).values_list("pk", flat=True))
    selected = request.session.get(SESSION_KEY)
    try:
        selected = int(selected)
    except (ValueError, TypeError):
        selected = None
    store_id = selected if selected in store_ids else (store_ids[0] if store_ids else None)
    has_promotions = False
    finance_visible = False
    if store_id is not None:
        if "promotion_audit" in capabilities:
            campaigns = PromotionSnapshot.objects.filter(
                account_id=store_id, fetched_at__isnull=False
            ).values_list("promotions", flat=True).first()
            has_promotions = isinstance(campaigns, list) and bool(campaigns)
        # A previously attached finance token must remain removable even if
        # the operator disables new finance connections.
        existing_finance = FinanceSnapshot.objects.filter(
            account_id=store_id, encrypted_finance_token__gt=""
        ).exists()
        finance_visible = existing_finance or (
            "finance_audit" in capabilities
            and settings.SAAS_WB_NETWORK_ENABLED
            and settings.NOSALE_WB_FINANCE_ENABLED
        )
    header_access = access_snapshot(request.user.pk)
    return {
        "seller_header_access": header_access,
        "unread_notification_count": count + digests,
        "seller_capabilities": capabilities,
        "seller_has_other_stores": len(store_ids) > 1,
        "seller_has_promotion_data": has_promotions,
        "seller_finance_available": finance_visible,
    }
