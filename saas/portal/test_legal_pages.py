"""Public NoSale legal pages and revised three-day trial acceptance.

The pages intentionally reuse verified BeClients operator identity while
describing NoSale-specific software, not the BeClients booking product.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from billing.models import AccessGrant, OwnerTariffOffer
from billing.trial import TRIAL_DAYS, grant_trial_once, trial_source_ref
from portal.legal_views import LEGAL_OPERATOR


@override_settings(
    TOCHKA_BILLING_ENABLED=False,
    NOSALE_FISCALIZATION_CONFIRMED=False,
)
class PublicLegalPagesTests(TestCase):
    def setUp(self):
        OwnerTariffOffer.objects.create(
            slug="standard", name="Standard", price_kopeks=50000, enabled=False,
        )
        OwnerTariffOffer.objects.create(
            slug="smart", name="Smart", price_kopeks=99000, enabled=False,
        )

    def test_all_documents_are_public_and_do_not_require_owner_or_seller_login(self):
        for url, heading in (
            ("/offer/", "Публичная оферта о доступе к NoSale"),
            ("/privacy/", "Политика в отношении обработки персональных данных NoSale"),
            ("/contacts/", "Контакты и реквизиты NoSale"),
        ):
            with self.subTest(path=url):
                response = self.client.get(url)
                self.assertContains(response, heading)
                self.assertContains(response, "Балабанов Сергей Валерьевич")
                self.assertContains(response, "910222422100")
                self.assertContains(response, "326366800019445")
                self.assertContains(response, "support@beclients.ru")
                self.assertContains(response, 'href="/offer/"')
                self.assertContains(response, 'href="/privacy/"')
                self.assertContains(response, 'href="/contacts/"')
                self.assertEqual(self.client.head(url).status_code, 200)
                self.assertEqual(self.client.post(url).status_code, 405)

    def test_public_documents_use_configured_domain(self):
        with override_settings(NOSALE_PUBLIC_BASE_URL="https://nosale.pro"):
            for path in ("/offer/", "/privacy/", "/contacts/"):
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertContains(response, "https://nosale.pro")
                    self.assertNotContains(response, "https://nosale.svdev.pro")

    def test_offer_truthful_trial_payment_and_wildberries_terms(self):
        self.assertEqual(TRIAL_DAYS, 3)
        response = self.client.get("/offer/")
        self.assertContains(response, "3 дня")
        self.assertContains(response, "первой успешной")
        self.assertContains(response, "Wildberries")
        self.assertContains(response, "DeepSeek")
        self.assertContains(response, "500,00 ₽")
        self.assertContains(response, "990,00 ₽")
        self.assertContains(response, "Приём платежей через сайт отключён")
        self.assertNotContains(response, "7 дней")
        self.assertNotContains(response, "БиКлайнтс")

    def test_privacy_is_about_sellers_and_marketplace_not_booking_clients(self):
        response = self.client.get("/privacy/")
        for term in ("API-токены Wildberries", "AI Platform/DeepSeek", "отозвать", "PIN"):
            self.assertContains(response, term)
        self.assertNotContains(response, "клиентов мастера")
        self.assertNotContains(response, "онлайн-записью")

    def test_contact_page_does_not_promise_live_acquiring(self):
        response = self.client.get("/contacts/")
        self.assertContains(response, "Оплата пока не подключена")
        self.assertContains(response, "500,00 ₽")
        self.assertContains(response, "990,00 ₽")
        self.assertContains(response, "3 дня")

    def test_existing_five_day_trial_is_not_shortened_on_new_three_day_policy(self):
        user = get_user_model().objects.create_user(username="legacy-three-day@example.test")
        start = timezone.now() - timedelta(days=1)
        existing_end = start + timedelta(days=5)
        existing = AccessGrant.objects.create(
            user=user, source="trial", source_ref=trial_source_ref(user.pk),
            starts_at=start, ends_at=existing_end,
        )
        original, created = grant_trial_once(user.pk, at=start + timedelta(hours=24))
        self.assertFalse(created)
        self.assertEqual(original.pk, existing.pk)
        self.assertEqual(original.ends_at, existing_end)
        self.assertEqual(AccessGrant.objects.filter(user=user, source="trial").count(), 1)
        new_user = get_user_model().objects.create_user(username="new-three-day@example.test")
        grant, created = grant_trial_once(new_user.pk, at=start)
        self.assertTrue(created)
        self.assertEqual(grant.ends_at, start + timedelta(days=3))

    def test_same_operator_identity_as_verified_beclients(self):
        self.assertEqual(LEGAL_OPERATOR["full_name"], "Балабанов Сергей Валерьевич")
        self.assertEqual(LEGAL_OPERATOR["inn"], "910222422100")
        self.assertEqual(LEGAL_OPERATOR["ogrnip"], "326366800019445")
        self.assertEqual(LEGAL_OPERATOR["email"], "support@beclients.ru")
