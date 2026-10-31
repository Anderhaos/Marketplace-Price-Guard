"""Small read-only product catalog; paid capabilities are enforced via AccessGrant.

Prices are per-deployment approved RUB values, NOT request/form-supplied.
Separate administrative product settings can replace this in the owner-admin stage.
"""
from decimal import Decimal

from .tochka import AcquiringUnavailable, approved_price_kopeks, configured
from .models import OwnerTariffOffer

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
    for slug, info in PLAN_DESCRIPTIONS.items():
        try:
            amount = approved_price_kopeks(slug)
        except AcquiringUnavailable:
            amount = None
        offers.append({
            "slug": slug,
            **info,
            "name": owner_offers[slug].name if slug in owner_offers else info["name"],
            "price_kopeks": amount,
            "price_rub": Decimal(amount) / 100 if amount is not None else None,
            "can_buy": configured() and amount is not None,
        })
    return offers
