"""Small read-only product catalog; paid capabilities are enforced via AccessGrant.

Prices are per-deployment approved RUB values, NOT request/form-supplied.
Separate administrative product settings can replace this in the owner-admin stage.
"""
from decimal import Decimal

from .tochka import AcquiringUnavailable, approved_price_kopeks, configured
from .models import OwnerTariffOffer
from .access import FEATURE_CHOICES, capabilities_for_plan

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
        modal_labels = (
            ["Всё из Standard"]
            + [label for code, label in FEATURE_CHOICES
               if code in active_features and code not in standard_features]
            if smart_extends_standard else feature_labels
        )
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
