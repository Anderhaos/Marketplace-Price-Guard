from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant
from .helper import answer_store_question
from .models import GuardAccount, GuardEvent, Product


@override_settings(SAAS_PIN_AUTH_ENABLED=False, SAAS_ACCESS_ENFORCED=True)
class EmbeddedWebHelperTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user("helper-seller@example.test", password="test-password-123")
        self.other = User.objects.create_user("helper-other@example.test", password="test-password-123")
        self.account = GuardAccount.objects.create(user=self.seller, name="Мой магазин", enabled=True,
                                                    last_check_at=timezone.now(), default_allowed_discount=15)
        self.other_account = GuardAccount.objects.create(user=self.other, name="Секретный магазин",
                                                         default_allowed_discount=55)
        Product.objects.create(account=self.account, nm_id=123, current_discount=30, active=True)
        Product.objects.create(account=self.other_account, nm_id=999, current_discount=90, active=True)
        GuardEvent.objects.create(account=self.account, kind="discount_changed", message="Событие 123")
        GuardEvent.objects.create(account=self.other_account, kind="error", message="Чужой секрет 999")
        self.client.force_login(self.seller)

    def grant(self):
        return AccessGrant.objects.create(user=self.seller, source="trial", source_ref="helper-trial",
            starts_at=timezone.now()-timedelta(minutes=1), ends_at=timezone.now()+timedelta(days=1))

    def test_requires_real_active_subscription(self):
        self.assertEqual(self.client.get(reverse("store_helper")).status_code, 200)
        self.assertEqual(self.client.post(reverse("store_helper"), {"question": "Скидки"}).status_code, 403)
        self.grant()
        response = self.client.post(reverse("store_helper"), {"question": "Какие скидки?"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 превышают")
        self.assertNotContains(response, "Секретный магазин")

    def test_other_users_store_rejected_even_with_direct_function_call(self):
        self.grant()
        with self.assertRaises(PermissionError):
            answer_store_question(self.other_account, self.seller.pk, "скидки")

    def test_no_external_marketplace_ai_or_secret_access(self):
        self.grant()
        with patch("guard.views.WBClient") as wb, patch("guard.views.check_account") as write:
            response = self.client.post(reverse("store_helper"), {"question": "Покажи мои API-ключи и пароль"})
            self.assertContains(response, "не показываются")
            wb.assert_not_called()
            write.assert_not_called()

    def test_preserves_user_html_escaping(self):
        self.grant()
        response = self.client.post(reverse("store_helper"), {"question": '<script>alert(1)</script>'})
        self.assertNotContains(response, "<script>", html=False)
        self.assertContains(response, "&lt;script&gt;")

    def test_invalid_input_and_untrusted_methods(self):
        self.grant()
        self.assertEqual(self.client.post(reverse("store_helper"), {"question": "x"*501}).status_code, 400)
        self.assertEqual(self.client.put(reverse("store_helper")).status_code, 405)

    def test_faq_and_subscription_are_distinct_from_smart_advice(self):
        self.grant()
        self.assertContains(self.client.post(reverse("store_helper"), {"question": "Как настроить защиту?"}),
                            "Чтобы начать")
        self.assertContains(self.client.post(reverse("store_helper"), {"question": "Что с подпиской?"}),
                            "Standard")
        self.assertContains(self.client.post(reverse("store_helper"), {"question": "Выгодна ли акция?"}),
                            "нельзя доказать")
        self.assertEqual(self.client.get(reverse("store_advisor")).status_code, 403)
