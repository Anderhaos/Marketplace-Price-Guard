"""R3: owner-managed Standard/Smart prices, immutable issued payment amounts."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AccessGrant, OwnerTariffOffer, SubscriptionPayment
from .service import apply_verified_event, create_checkout
from .tochka import AcquiringUnavailable, approved_price_kopeks


@override_settings(
    TOCHKA_BILLING_ENABLED=True,
    NOSALE_MONTHLY_PRICE_RUB="500.00",
    NOSALE_STANDARD_MONTHLY_PRICE_RUB=None,
    NOSALE_SMART_MONTHLY_PRICE_RUB="990.00",
    TOCHKA_CUSTOMER_CODE="owner-price-test",
    TOCHKA_MERCHANT_ID="owner-price-merchant",
    SAAS_PIN_AUTH_ENABLED=False,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class OwnerTariffOfferTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="owner-price@example.test", email="owner-price@example.test", password="test"
        )
        self.staff = User.objects.create_user(
            username="staff-price@example.test", email="staff-price@example.test",
            password="test", is_staff=True,
        )
        self.seller = User.objects.create_user(
            username="seller-price@example.test", email="seller-price@example.test", password="test"
        )
        self.bank = patch(
            "billing.service.create_tochka_link",
            side_effect=lambda payment: ("https://merch.tochka.com/order/test", f"op-{payment.pk}"),
        )
        self.bank.start()
        self.addCleanup(self.bank.stop)

    def bank_event(self, pay):
        return {"data": {
            "webhookType": "acquiringInternetPayment", "operationId": pay.operation_id,
            "paymentLinkId": str(pay.pk), "merchantId": "owner-price-merchant",
            "customerCode": "owner-price-test", "amount": str(pay.amount_rubles),
            "status": "APPROVED",
        }}

    def test_env_fallback_until_owner_creates_tariff(self):
        self.assertEqual(approved_price_kopeks("standard"), 50000)
        self.assertEqual(approved_price_kopeks("smart"), 99000)

    def test_owner_override_is_source_for_only_new_quotes(self):
        standard = create_checkout(self.seller.pk, plan="standard")
        self.assertEqual(standard.amount_kopeks, 50000)
        offer = OwnerTariffOffer.objects.create(
            slug="standard", name="Standard", price_kopeks=60000, enabled=True,
        )
        replacement = create_checkout(self.seller.pk, plan="standard")
        self.assertEqual(replacement.amount_kopeks, 60000)
        self.assertNotEqual(standard.pk, replacement.pk)
        offer.price_kopeks = 70000
        offer.save()
        self.assertEqual(approved_price_kopeks("standard"), 70000)
        standard.refresh_from_db()
        replacement.refresh_from_db()
        self.assertEqual(standard.amount_kopeks, 50000)
        self.assertEqual(replacement.amount_kopeks, 60000)
        self.assertEqual(apply_verified_event(self.bank_event(standard)), "activated")
        self.assertEqual(apply_verified_event(self.bank_event(replacement)), "activated")
        self.assertEqual(AccessGrant.objects.filter(user=self.seller, source="payment").count(), 2)

    def test_owner_disables_smart_without_affecting_standard(self):
        offer = OwnerTariffOffer.objects.create(
            slug="smart", name="Smart", price_kopeks=99000, enabled=False,
        )
        with self.assertRaises(AcquiringUnavailable):
            create_checkout(self.seller.pk, plan="smart")
        self.assertEqual(approved_price_kopeks("standard"), 50000)
        offer.enabled = True
        offer.save()
        self.assertEqual(create_checkout(self.seller.pk, plan="smart").plan, "smart")

    def test_only_superuser_can_manage_offers_in_django_admin(self):
        add = reverse("admin:billing_ownertariffoffer_add")
        list_url = reverse("admin:billing_ownertariffoffer_changelist")
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(list_url).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(add).status_code, 200)
        response = self.client.post(add, {
            "slug": "smart", "name": "Smart",
            "price_rub": "990.00", "enabled": "on", "_save": "Save",
        })
        self.assertEqual(response.status_code, 302, str(getattr(response, "context", "")))
        offer = OwnerTariffOffer.objects.get(slug="smart")
        self.assertEqual(offer.price_kopeks, 99000)
        self.assertTrue(offer.enabled)
        self.assertEqual(approved_price_kopeks("smart"), 99000)
        change = reverse("admin:billing_ownertariffoffer_change", args=["smart"])
        self.assertEqual(self.client.get(change).status_code, 200)
        self.client.post(change, {
            "name": "Smart Plus", "price_rub": "890.50", "enabled": "on",
            "_save": "Save",
        })
        offer.refresh_from_db()
        self.assertEqual((offer.name, offer.price_kopeks), ("Smart Plus", 89050))
        self.assertEqual(self.client.get(reverse("admin:billing_ownertariffoffer_delete", args=["smart"])).status_code, 403)

    def test_invalid_owner_price_does_not_mutate_offer(self):
        self.client.force_login(self.owner)
        offer = OwnerTariffOffer.objects.create(slug="standard", name="Standard", price_kopeks=50000, enabled=True)
        url = reverse("admin:billing_ownertariffoffer_change", args=[offer.pk])
        response = self.client.post(url, {"name": "Standard", "price_rub": "0", "enabled": "on", "_save": "Save"})
        self.assertEqual(response.status_code, 200)
        offer.refresh_from_db()
        self.assertEqual(offer.price_kopeks, 50000)
