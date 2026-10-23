"""Regression tests for the unified NoSale welcome / email-first entry."""
import re
from django.contrib.auth.models import User
from django.core import mail
from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings

@override_settings(
    SAAS_PIN_AUTH_ENABLED=True,
    REQUIRE_EMAIL_VERIFICATION=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    SAAS_WB_NETWORK_ENABLED=False,
)
class EmailFirstWelcomeTests(TestCase):
    def test_first_screen_explains_product_and_asks_only_for_email(self):
        response = self.client.get("/login/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Три простых шага")
        self.assertContains(response, "5 дней бесплатно")
        self.assertContains(response, 'name="email"')
        self.assertNotContains(response, 'name="pin"')
        self.assertContains(response, 'class="welcome-title-secondary"')
        self.assertContains(response, "Даже когда вы отдыхаете.")
        self.assertContains(response, 'class="welcome-trial-card"')
        html = response.content.decode()
        self.assertLess(html.index("NoSale проверяет скидки"), html.index("5 дней бесплатно"))
        self.assertLess(html.index("5 дней бесплатно"), html.index("Три простых шага"))
        self.assertEqual(html.count("5 дней бесплатно"), 1)
        self.assertNotContains(response, "Начать бесплатно")
        self.assertEqual(html.count('class="welcome-submit"'), 1)

    def test_known_email_moves_to_pin_without_sending_message(self):
        user = User.objects.create_user(
            username="known@example.test", email="known@example.test", password="6124"
        )
        response = self.client.post("/login/", {"email": "KNOWN@example.test"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Введите ваш PIN")
        self.assertContains(response, 'name="pin"')
        self.assertEqual(len(mail.outbox), 0)
        self.assertRedirects(
            self.client.post("/login/", {"email": user.email, "pin": "6124"}), "/"
        )

    def test_new_email_starts_existing_verified_registration(self):
        response = self.client.post("/login/", {"email": "new@example.test"})
        self.assertRedirects(response, "/auth/code/")
        self.assertEqual(len(mail.outbox), 1)
        code = re.search(r"Код: (\d{4})", mail.outbox[0].body)
        self.assertIsNotNone(code)
        self.assertRedirects(self.client.post("/auth/code/", {"code": code.group(1)}), "/auth/pin/")
        self.assertRedirects(
            self.client.post("/auth/pin/", {"pin": "9184", "confirm_pin": "9184"}), "/"
        )
        self.assertTrue(User.objects.filter(email="new@example.test").exists())

    def test_bad_email_cannot_skip_initial_step(self):
        response = self.client.post("/login/", {"email": "not an address"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Введите правильный адрес электронной почты")
        self.assertEqual(len(mail.outbox), 0)

    def test_failed_pin_stays_on_pin_step_and_never_logs_in(self):
        user = User.objects.create_user(
            username="known2@example.test", email="known2@example.test", password="9184"
        )
        response = self.client.post("/login/", {"email": user.email, "pin": "0000"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неверный email или PIN")
        self.assertContains(response, 'name="pin"')
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_welcome_stylesheet_is_discoverable(self):
        path = finders.find("welcome.css")
        self.assertIsNotNone(path)
        with open(path, encoding="utf-8") as file:
            self.assertIn(".welcome-layout", file.read())

    def test_code_verification_has_contextual_two_column_guide(self):
        self.client.post("/login/", {"email": "guide@example.test"})
        response = self.client.get("/auth/code/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="welcome-layout auth-flow-layout"')
        self.assertContains(response, "Проверьте вашу")
        self.assertContains(response, "guide@example.test")
        self.assertContains(response, "Код действует 10 минут")
        self.assertContains(response, "После подтверждения останется придумать PIN")
        self.assertContains(response, 'name="code"')
        self.assertContains(response, "Подтвердить и продолжить")
        self.assertNotContains(response, "Начать бесплатно")

    def test_pin_setup_continues_conversation_after_verifying_email(self):
        self.client.post("/login/", {"email": "new-guided@example.test"})
        code = re.search(r"Код: (\d{4})", mail.outbox[-1].body).group(1)
        self.assertRedirects(self.client.post("/auth/code/", {"code": code}), "/auth/pin/")
        response = self.client.get("/auth/pin/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="welcome-layout auth-flow-layout"')
        self.assertContains(response, "Почта подтверждена")
        self.assertContains(response, "new-guided@example.test")
        self.assertContains(response, "Шаг 3 из 3")
        self.assertContains(response, 'name="pin"')
        self.assertContains(response, 'name="confirm_pin"')
        self.assertContains(response, "Создать аккаунт")

    def test_recovery_guide_does_not_claim_unknown_email_was_sent(self):
        self.client.post("/password-reset/", {"email": "unknown-guided@example.test"})
        response = self.client.get("/auth/code/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Если этот адрес связан с аккаунтом")
        self.assertContains(response, "Восстановление доступа")
        self.assertEqual(len(mail.outbox), 0)

    def test_known_account_pin_screen_has_its_own_contextual_guide(self):
        User.objects.create_user(
            username="known-guide@example.test", email="known-guide@example.test", password="6159"
        )
        response = self.client.post("/login/", {"email": "known-guide@example.test"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "С возвращением")
        self.assertContains(response, "Аккаунт найден")
        self.assertContains(response, "Остался ваш PIN")
        self.assertContains(response, 'name="pin"')
