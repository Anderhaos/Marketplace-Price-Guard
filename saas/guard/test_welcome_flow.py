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
