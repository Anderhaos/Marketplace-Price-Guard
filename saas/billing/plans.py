"""Small read-only product catalog; paid capabilities are enforced via AccessGrant.

Prices are per-deployment approved RUB values, NOT request/form-supplied.
Separate administrative product settings can replace this in the owner-admin stage.
"""
from decimal import Decimal

from django.conf import settings

from .tochka import AcquiringUnavailable, approved_price_kopeks, configured
from .models import OwnerTariffOffer
from .access import FEATURE_CHOICES, capabilities_for_plan

# Concise, actual feature claims for the expired-trial purchase screen.
# Never promise disabled WB Finance or unconditional DeepSeek availability.
PAYWALL_BENEFITS = {
    "discount_guard": "Контроль скидок по вашим лимитам",
    "notifications": "Уведомления и отчёты",
    "activity_history": "История проверок и изменений",
    "promotion_audit": "Проверка акций Wildberries",
    "finance_audit": "Финансовые отчёты Wildberries",
    "store_advice": "Персональные рекомендации по расписанию",
    "ai_advice": "AI-резюме при включённом DeepSeek",
}

PLAN_DESCRIPTIONS = {
    "standard": {
        "name": "Standard",
        "description": "Контроль скидок Wildberries, журнал событий и регулярные базовые уведомления.",
    },
    "smart": {
        "name": "Smart",
        "description": "Всё из Standard и до двух персональных рекомендательных сводок в день. Аналитика рассчитывается сервисом, DeepSeek формулирует краткое резюме. Свободного AI-чата нет.",
    },
}


def tariff_offers():
    offers = []
    owner_offers = OwnerTariffOffer.objects.in_bulk(field_name="slug")
    standard_features = capabilities_for_plan("standard")
    for slug, info in PLAN_DESCRIPTIONS.items():
        active_features = capabilities_for_plan(slug)
        owner_offer = owner_offers.get(slug)
        try:
            amount = approved_price_kopeks(slug)
        except AcquiringUnavailable:
            amount = None

        # A disabled offer may still show its owner-entered indicative price.
        # Never allow that price to become a checkout quote: approved_price_kopeks
        # remains the sole bank-side source of truth.
        display_amount = amount
        if (display_amount is None and owner_offer is not None
                and 100 <= owner_offer.price_kopeks <= 10_000_000):
            display_amount = owner_offer.price_kopeks

        feature_labels = [label for code, label in FEATURE_CHOICES if code in active_features]
        smart_extends_standard = (
            slug == "smart" and standard_features
            and standard_features.issubset(active_features)
        )
        visible_features = (
            active_features if settings.NOSALE_WB_FINANCE_ENABLED
            else active_features - {"finance_audit"}
        )
        if smart_extends_standard:
            extras = visible_features - standard_features
            modal_labels = ["Всё из Standard"] + [
                PAYWALL_BENEFITS[code] for code, _ in FEATURE_CHOICES
                if code in extras and code in PAYWALL_BENEFITS
            ]
        else:
            modal_labels = [
                PAYWALL_BENEFITS[code] for code, _ in FEATURE_CHOICES
                if code in visible_features and code in PAYWALL_BENEFITS
            ]
        offers.append({
            "slug": slug,
            **info,
            "description": "Функции тарифа задаёт владелец; актуальный состав приведён ниже.",
            "feature_labels": feature_labels,
            "modal_labels": modal_labels,
            "name": owner_offer.name if owner_offer is not None else info["name"],
            "price_kopeks": amount,
            "price_rub": Decimal(display_amount) / 100 if display_amount is not None else None,
            "can_buy": configured() and amount is not None,
        })
    return offers
