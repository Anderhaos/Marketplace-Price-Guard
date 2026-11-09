from django.conf import settings
from .models import GuardAccount, PromotionSnapshot, FinanceSnapshot
from .stores import SESSION_KEY
from billing.access import capabilities_for_user
from billing.trial import access_snapshot
from billing.plans import tariff_offers


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
    # A burst of identical WB 429 responses is one unread incident,
    # not dozens of unread messages in the header.
    from .notifications_feed import unread_notification_groups
    unread_count = unread_notification_groups(request.user)

    # Use the seller's OWN selected store. Never leak another account's
    # campaigns or show empty / operator-disabled feature screens.
    seller_stores = list(
        GuardAccount.objects.filter(user=request.user, marketplace="wb")
        .order_by("id").values("id", "name")
    )
    store_ids = [store["id"] for store in seller_stores]
    selected = request.session.get(SESSION_KEY)
    try:
        selected = int(selected)
    except (ValueError, TypeError):
        selected = None
    store_id = selected if selected in store_ids else (store_ids[0] if store_ids else None)
    active_store = next((store for store in seller_stores if store["id"] == store_id), None)
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
    # Only expired accounts need the read-only tariff catalog for the dialog.
    trial_offers = tariff_offers() if header_access["trial_expired"] else []
    return {
        "seller_trial_offers": trial_offers,
        "seller_trial_payment_available": any(offer["can_buy"] for offer in trial_offers),
        "seller_header_access": header_access,
        "seller_header_store_name": active_store["name"] if active_store else None,
        "seller_header_store_id": store_id,
        "seller_header_wb_stores": seller_stores,
        "unread_notification_count": unread_count,
        "seller_capabilities": capabilities,
        "seller_has_other_stores": len(store_ids) > 1,
        "seller_has_promotion_data": has_promotions,
        "seller_finance_available": finance_visible,
    }
