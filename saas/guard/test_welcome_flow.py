"""Regression tests for the unified NoSale welcome / email-first entry."""
import re
import os
from unittest.mock import patch
from django.db.utils import OperationalError
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
        self.assertContains(response, "3 дня бесплатно")
        self.assertContains(response, 'name="email"')
        self.assertNotContains(response, 'name="pin"')
        self.assertContains(response, 'class="welcome-title-secondary"')
        self.assertContains(response, "Даже когда вы отдыхаете.")
        self.assertContains(response, 'class="welcome-trial-card"')
        html = response.content.decode()
        self.assertLess(html.index("NoSale проверяет скидки"), html.index("3 дня бесплатно"))
        self.assertLess(html.index("3 дня бесплатно"), html.index("Три простых шага"))
        self.assertEqual(html.count("3 дня бесплатно"), 1)
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
        self.assertContains(response, "Код действителен 10 минут")
        self.assertContains(response, "Доступно 5 попыток ввода")
        html = response.content.decode()
        self.assertEqual(html.count("Код действителен 10 минут"), 1)
        self.assertLess(html.index("Подтвердить и продолжить"), html.index("Код действителен 10 минут"))
        self.assertLess(html.index("Код действителен 10 минут"), html.index("</form>", html.index("Подтвердить и продолжить")))
        self.assertIn('class="auth-flow-code-note"', html)
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

    def test_pin_submission_is_idempotent_after_sign_up(self):
        self.client.post("/login/", {"email": "doubleclick@example.test"})
        challenge_code = re.search(r"Код: (\d{4})", mail.outbox[-1].body).group(1)
        self.assertRedirects(self.client.post("/auth/code/", {"code": challenge_code}), "/auth/pin/")
        response = self.client.post("/auth/pin/", {"pin": "7382", "confirm_pin": "7382"})
        self.assertRedirects(response, "/")
        self.assertTrue(User.objects.filter(email="doubleclick@example.test").exists())
        # Refresh / duplicate POST no longer takes a registered seller back
        # to the registration step after the challenge is consumed.
        self.assertRedirects(
            self.client.post("/auth/pin/", {"pin": "7382", "confirm_pin": "7382"}), "/"
        )
        self.assertRedirects(self.client.get("/auth/pin/"), "/")
        self.assertEqual(User.objects.filter(email="doubleclick@example.test").count(), 1)

    def test_transient_database_lock_shows_retry_message_without_consuming_verification(self):
        self.client.post("/login/", {"email": "db-lock@example.test"})
        challenge_code = re.search(r"Код: (\d{4})", mail.outbox[-1].body).group(1)
        self.assertRedirects(self.client.post("/auth/code/", {"code": challenge_code}), "/auth/pin/")
        with patch("guard.email_pin.User.save", side_effect=OperationalError("database is locked")):
            response = self.client.post("/auth/pin/", {"pin": "7382", "confirm_pin": "7382"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Не удалось сохранить PIN из-за временной ошибки базы")
        self.assertFalse(User.objects.filter(email="db-lock@example.test").exists())
        self.assertRedirects(
            self.client.post("/auth/pin/", {"pin": "7382", "confirm_pin": "7382"}), "/"
        )
        self.assertEqual(User.objects.filter(email="db-lock@example.test").count(), 1)

    @override_settings(
        DEBUG=True,
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        SAAS_WB_NETWORK_ENABLED=False,
        TOCHKA_BILLING_ENABLED=False,
    )
    def test_localhost_only_session_code_allows_full_preview_registration(self):
        with patch.dict(os.environ, {"NOSALE_LOCAL_EMAIL_PREVIEW": "1"}):
            r = self.client.post(
                "/login/", {"email": "preview-guide@example.test"},
                HTTP_HOST="127.0.0.1:8926",
            )
            self.assertEqual(r.status_code, 302)
            self.assertEqual(r["Location"], "/auth/code/")
            code = self.client.session["mpno_saas_test_preview_code"]["code"]
            self.assertRegex(code, r"^\d{4}$")
            r = self.client.get("/auth/code/", HTTP_HOST="127.0.0.1:8926")
            self.assertContains(r, "Только тестовая версия")
            self.assertContains(r, code)
            self.assertContains(r, "настоящее письмо здесь не отправляется")
            # Production/non-loopback origins must never display the test OTP,
            # even when the feature flag is accidentally present.
            r = self.client.get("/auth/code/", HTTP_HOST="testserver")
            self.assertNotContains(r, 'class="auth-flow-test-digits"')
            with override_settings(SAAS_WB_NETWORK_ENABLED=True, SAAS_WB_READ_ONLY=False):
                r = self.client.get("/auth/code/", HTTP_HOST="127.0.0.1:8926")
                self.assertNotContains(r, 'class="auth-flow-test-digits"')
            with override_settings(SAAS_WB_NETWORK_ENABLED=True, SAAS_WB_READ_ONLY=True):
                r = self.client.get("/auth/code/", HTTP_HOST="127.0.0.1:8926")
                self.assertContains(r, 'class="auth-flow-test-digits"')
                self.assertContains(r, code)
                with override_settings(DEBUG=False):
                    self.assertNotContains(
                        self.client.get("/auth/code/", HTTP_HOST="127.0.0.1:8926"),
                        'class="auth-flow-test-digits"',
                    )
                with override_settings(TOCHKA_BILLING_ENABLED=True):
                    self.assertNotContains(
                        self.client.get("/auth/code/", HTTP_HOST="127.0.0.1:8926"),
                        'class="auth-flow-test-digits"',
                    )
                with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
                    self.assertNotContains(
                        self.client.get("/auth/code/", HTTP_HOST="127.0.0.1:8926"),
                        'class="auth-flow-test-digits"',
                    )
            r = self.client.post(
                "/auth/code/", {"code": code}, HTTP_HOST="127.0.0.1:8926"
            )
            self.assertEqual(r.status_code, 302)
            self.assertEqual(r["Location"], "/auth/pin/")
            self.assertNotIn("mpno_saas_test_preview_code", self.client.session)
            r = self.client.post(
                "/auth/pin/", {"pin": "8372", "confirm_pin": "8372"},
                HTTP_HOST="127.0.0.1:8926",
            )
            self.assertEqual(r.status_code, 302)
            self.assertEqual(User.objects.filter(email="preview-guide@example.test").count(), 1)

    def test_pin_creation_has_accessible_visibility_buttons_for_both_fields(self):
        self.client.post("/login/", {"email": "eye-create@example.test"})
        challenge_code = re.search(r"Код: (\d{4})", mail.outbox[-1].body).group(1)
        self.assertRedirects(self.client.post("/auth/code/", {"code": challenge_code}), "/auth/pin/")
        response = self.client.get("/auth/pin/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'src="/static/pin-visibility.js"')
        self.assertContains(response, 'data-pin-visibility-root')
        self.assertContains(response, 'data-pin-secret', count=2)
        self.assertContains(response, 'data-pin-toggle', count=2)
        self.assertContains(response, 'aria-controls="auth-new-pin"')
        self.assertContains(response, 'aria-controls="auth-confirm-pin"')
        self.assertContains(response, 'aria-label="Показать PIN"', count=2)
        self.assertContains(response, 'type="button" data-pin-toggle', count=2)
        self.assertContains(response, 'name="pin"')
        self.assertContains(response, 'name="confirm_pin"')
        self.assertIsNotNone(finders.find("pin-visibility.js"))
        self.assertIsNotNone(finders.find("welcome.css"))

    def test_returning_seller_pin_input_has_one_visibility_button(self):
        User.objects.create_user(
            username="eye-login@example.test",
            email="eye-login@example.test",
            password="6819",
        )
        response = self.client.post("/login/", {"email": "eye-login@example.test"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-pin-visibility-root')
        self.assertContains(response, 'data-pin-secret', count=1)
        self.assertContains(response, 'data-pin-toggle', count=1)
        self.assertContains(response, 'aria-controls="welcome-pin"')
        self.assertContains(response, 'maxlength="4"')
        self.assertContains(response, 'type="button" data-pin-toggle', count=1)
        self.assertNotContains(response, "pin-visibility-toggle" + " type=\"submit\"")
