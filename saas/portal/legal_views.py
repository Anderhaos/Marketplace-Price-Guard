"""Public NoSale documents adapted from BeClients/MiniBook legal pages.

The operator identity and contact are copied from the public live BeClients
site-config (verified 2026-10-31), not inferred from unrelated seller records.
When the legal operator changes, review ALL pages and bump the edition.
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
    "postal_address": "394029, Воронеж, Ленинградская ул., 136, а/я 27",
    # Verified shared operator mailbox from https://beclients.ru/site-config.snapshot.json.
    # Do not invent a dedicated @nosale mailbox without verifying delivery.
    "email": "support@beclients.ru",
}
LEGAL_EDITION = "1.0"
LEGAL_EDITION_DATE = "31.10.2026"


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
