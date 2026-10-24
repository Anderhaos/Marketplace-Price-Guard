"""Small read-only product catalog; paid capabilities are enforced via AccessGrant.

Prices are per-deployment approved RUB values, NOT request/form-supplied.
Separate administrative product settings can replace this in the owner-admin stage.
"""
from decimal import Decimal

from .tochka import AcquiringUnavailable, approved_price_kopeks, configured

PLAN_DESCRIPTIONS = {
    "standard": {
        "name": "Standard",
        "description": "Защита скидок Wildberries, события и базовый помощник в кабинете.",
    },
    "smart": {
        "name": "Smart",
        "description": "Всё из Standard, AI-ассистент с фактами вашего магазина и ограниченными рекомендациями.",
    },
}


def tariff_offers():
    offers = []
    for slug, info in PLAN_DESCRIPTIONS.items():
        try:
            amount = approved_price_kopeks(slug)
        except AcquiringUnavailable:
            amount = None
        offers.append({
            "slug": slug,
            **info,
            "price_kopeks": amount,
            "price_rub": Decimal(amount) / 100 if amount is not None else None,
            "can_buy": configured() and amount is not None,
        })
    return offers
