"""Local-only operational readiness for the owner console.

Never performs active WB, AI Platform, payment or MCP requests. A historic
verification is explicitly not a live availability or commercial readiness claim.
Never decrypts secrets or reflects provider messages to HTML.
"""
from django.conf import settings
from django.contrib.auth import get_user_model

from billing.models import OwnerAISettings, OwnerAIGatewayConnection, OwnerMCPSettings, OwnerTariffOffer, OwnerOperationsSettings, OwnerMCPAdapter
from guard.models import GuardAccount
from push.models import PushDelivery


def integration_readiness():
    """Bounded, per-component source-of-truth configuration snapshot."""
    ai = OwnerAISettings.objects.filter(pk=1).first()
    gateway = OwnerAIGatewayConnection.objects.filter(pk=1).first()
    mcp = OwnerMCPSettings.runtime()
    adapters = list(OwnerMCPAdapter.objects.filter(slug__in=("wb", "ozon")).order_by("slug"))
    operations = OwnerOperationsSettings.runtime()
    model = (ai.gateway_model_id or "").strip() if ai else ""
    catalog = gateway.catalog_models if gateway and isinstance(gateway.catalog_models, list) else []
    verified_model = bool(
        gateway and gateway.configured and gateway.last_check_ok is True
        and model and model in catalog
    )
    ai_ready = bool(ai and ai.ready_for_gateway and verified_model)
    enabled_tools = [
        tool for tool in ("wb_overview", "wb_products", "wb_events", "wb_promotions")
        if mcp and mcp.allows(tool)
    ]
    wb_network = bool(settings.SAAS_WB_NETWORK_ENABLED)
    wb_write = bool(wb_network and not settings.SAAS_WB_READ_ONLY)
    acquiring = bool(settings.TOCHKA_BILLING_ENABLED)
    fiscal = bool(settings.NOSALE_FISCALIZATION_CONFIRMED)
    configured_rates = OwnerTariffOffer.objects.filter(enabled=True).count()
    seller_count = get_user_model().objects.filter(is_active=True, is_superuser=False).count()
    stores = GuardAccount.objects.values("marketplace")
    wb_stores = stores.filter(marketplace="wb").count()
    ozon_stores = stores.filter(marketplace="ozon").count()
    undelivered = PushDelivery.objects.filter(state__in=("pending", "failed")).count()
    return {
        "mcp": {
            "state": "enabled" if mcp and mcp.enabled else "disabled",
            "tools": enabled_tools,
            "tool_count": len(enabled_tools),
            "ozon_status": bool(mcp and mcp.allows("ozon_adapter_status")),
            "adapters": [
                {"slug": item.slug, "enabled": item.enabled,
                 "implementation": item.implementation}
                for item in adapters
            ],
            "max_products": mcp.max_products if mcp else None,
            "last_change": mcp.updated_at if mcp else None,
        },
        "wb": {
            "network_allowed": wb_network,
            "writes_allowed": wb_write,
            "store_count": wb_stores,
        },
        "ozon": {
            "store_count": ozon_stores,
            "api_implemented": False,
        },
        "ai": {
            "switch_on": bool(ai and ai.ai_enabled),
            "key_saved": bool(gateway and gateway.configured),
            "last_verified": bool(gateway and gateway.last_check_ok is True),
            "last_checked_at": gateway.last_checked_at if gateway else None,
            "selected_model": model or "Не выбрана",
            "model_verified": verified_model,
            "monthly_budget_kopeks": ai.monthly_budget_kopeks if ai else 0,
            "usable": ai_ready,
            "verified_model_count": len(catalog) if gateway and gateway.last_check_ok else 0,
        },
        "payments": {
            "enabled_by_deploy": acquiring,
            "fiscalization_confirmed": fiscal,
            "enabled_tariffs": configured_rates,
            # This is NOT a bank payment execution / fiscal receipt test.
            "commercial_ready": False,
        },
        "identity": {
            "pin_enabled": bool(settings.SAAS_PIN_AUTH_ENABLED),
            "email_verification": bool(settings.REQUIRE_EMAIL_VERIFICATION),
            "delivery_mode": (
                "console_test_only"
                if settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend"
                else "configured_unverified"
            ),
            "seller_count": seller_count,
        },
        "operations": {
            "configured": operations is not None,
            "guard_checks_enabled": bool(operations and operations.guard_checks_enabled),
            "instant_events_enabled": bool(operations and operations.instant_events_enabled),
            "daily_digests_enabled": bool(operations and operations.daily_digests_enabled),
            "push_delivery_enabled": bool(operations and operations.push_delivery_enabled),
            "guard_poll_seconds": operations.guard_poll_seconds if operations else None,
            "notifications_poll_seconds": operations.notifications_poll_seconds if operations else None,
            "push_batch_size": operations.push_batch_size if operations else None,
            # Local DB switch is not a claim of WB, mail, or push network health.
        },
        "notifications": {
            "undelivered_count": undelivered,
            "live_gateway_verified": False,
        },
    }
