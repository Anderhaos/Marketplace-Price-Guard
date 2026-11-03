"""Public legal documents for NoSale.

The operating entity remains unchanged. NoSale uses its own public support
address rather than another product's address. Verify that this mailbox can
receive requests before enabling commercial sales.
These pages do not enable acquiring or fiscalization.
"""
from decimal import Decimal

from django.conf import settings
from django.shortcuts import render
from django.views.decorators.http import require_safe

from billing.models import OwnerTariffOffer
from billing.trial import TRIAL_DAYS

LEGAL_OPERATOR = {
    "entity_type": "ИП",
    "full_name": "Балабанов Сергей Валерьевич",
    "inn": "910222422100",
    "ogrnip": "326366800019445",
    # Dedicated NoSale contact; mailbox delivery must be independently verified.
    "email": "support@nosale.pro",
}
LEGAL_EDITION = "1.3"
LEGAL_EDITION_DATE = "03.11.2026"


def _legal_context():
    tariff_rows = OwnerTariffOffer.objects.filter(
        slug__in=("standard", "smart"),
    ).order_by("slug").values("slug", "name", "price_kopeks", "enabled")
    offers = [
        {
            "slug": row["slug"],
            "name": row["name"],
            "price_rub": Decimal(row["price_kopeks"]) / 100,
            "sale_enabled": bool(row["enabled"]),
        }
        for row in tariff_rows
    ]
    return {
        "legal": LEGAL_OPERATOR,
        "edition": LEGAL_EDITION,
        "edition_date": LEGAL_EDITION_DATE,
        "site_url": settings.NOSALE_PUBLIC_BASE_URL,
        "trial_days": TRIAL_DAYS,
        "offers": offers,
        # Owner toggles alone do not make a payable offer; acquiring/fiscal
        # features are intentionally not assumed active.
        "payments_open": bool(
            settings.TOCHKA_BILLING_ENABLED and settings.NOSALE_FISCALIZATION_CONFIRMED
        ),
    }


@require_safe
def offer(request):
    return render(request, "legal/offer.html", _legal_context())


@require_safe
def privacy(request):
    return render(request, "legal/privacy.html", _legal_context())


@require_safe
def contacts(request):
    return render(request, "legal/contacts.html", _legal_context())
