"""R2: functional Standard web assistant, authenticated and seller-scoped."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.access import has_plan_capability
from billing.models import AccessGrant
from .assistant import basic_assistant_reply
from .models import AssistantMessage, GuardAccount, GuardEvent, Product, Upload
from .stores import SESSION_KEY


@override_settings(SAAS_PIN_AUTH_ENABLED=False, SAAS_ACCESS_ENFORCED=True)
class WebAssistantTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user("assist@example.test", "assist@example.test", "test-pass")
        self.other = User.objects.create_user("other-assist@example.test", "other-assist@example.test", "test-pass")
        self.store = GuardAccount.objects.create(
            user=self.seller, name="Мой магазин", token_ciphertext="opaque",
            initial_sync_completed_at=timezone.now(), last_check_at=timezone.now(),
            default_allowed_discount=20,
        )
        self.foreign = GuardAccount.objects.create(
            user=self.other, name="Чужой секретный магазин", default_allowed_discount=1,
        )
        Product.objects.create(account=self.store, nm_id=112, current_discount=35)
        Product.objects.create(account=self.store, nm_id=113, current_discount=5)
        Product.objects.create(account=self.foreign, nm_id=999, current_discount=98)
        GuardEvent.objects.create(account=self.store, kind="price_changed", message="Changed WB 112")
        GuardEvent.objects.create(account=self.foreign, kind="price_changed", message="Secret sale")
        self.client.force_login(self.seller)

    def grant(self, *, user=None, plan="standard", expired=False):
        user = user or self.seller
        now = timezone.now()
        return AccessGrant.objects.create(
            user=user, plan=plan, source="admin", source_ref=f"web-chat:{user.pk}:{now.timestamp()}",
            starts_at=now - timedelta(days=2),
            ends_at=now - timedelta(seconds=1) if expired else now + timedelta(days=3),
        )

    def test_requires_authentication_and_effective_access(self):
        url = reverse("assistant_chat")
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.seller)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"question": "статус"}).status_code, 403)
        self.grant(expired=True)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.grant()
        self.assertTrue(has_plan_capability(self.seller.pk, "web_assistant_basic"))
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_answers_are_factual_and_scoped_to_selected_store(self):
        self.grant()
        url = reverse("assistant_chat")
        self.assertEqual(self.client.post(url, {"question": "Сколько товаров?"}).status_code, 302)
        response = self.client.get(url)
        self.assertContains(response, "2 активных товаров")
        self.assertNotContains(response, "Чужой секретный магазин")
        self.assertEqual(self.client.post(url, {"question": "Сколько выше лимита скидки?"}).status_code, 302)
        response = self.client.get(url)
        self.assertContains(response, "предела скидки — 1")
        self.assertContains(response, "20%")
        self.assertNotContains(response, "999")

    def test_tampered_store_id_never_selects_other_seller(self):
        self.grant()
        session = self.client.session
        session[SESSION_KEY] = self.foreign.pk
        session.save()
        self.assertEqual(self.client.post(reverse("assistant_chat"), {"question": "товары"}).status_code, 302)
        msg = AssistantMessage.objects.get()
        self.assertEqual(msg.user_id, self.seller.pk)
        self.assertEqual(msg.account_id, self.store.pk)
        self.assertNotIn("Чужой", msg.answer)
        with self.assertRaises(PermissionError):
            basic_assistant_reply(self.seller.pk, self.foreign, "статус")

    def test_messages_are_private_even_for_two_connected_stores(self):
        self.grant()
        new_store = GuardAccount.objects.create(user=self.seller, name="Второй магазин")
        AssistantMessage.objects.create(user=self.seller, account=self.store, question="старый", answer="ответ")
        session = self.client.session
        session[SESSION_KEY] = new_store.pk
        session.save()
        response = self.client.get(reverse("assistant_chat"))
        self.assertNotContains(response, "старый")
        self.assertContains(response, "Второй магазин")
        self.client.force_login(self.other)
        self.grant(user=self.other)
        response = self.client.get(reverse("assistant_chat"))
        self.assertNotContains(response, "старый")

    def test_escape_untrusted_store_name_and_user_input(self):
        self.grant()
        self.store.name = "<img src=x onerror=alert(1)>"
        self.store.save(update_fields=["name"])
        self.assertEqual(self.client.post(reverse("assistant_chat"), {
            "question": "<script>alert(1)</script>"
        }).status_code, 302)
        resp = self.client.get(reverse("assistant_chat"))
        self.assertContains(resp, "&lt;script&gt;alert(1)&lt;/script&gt;", html=False)
        self.assertNotContains(resp, "<script>alert(1)</script>")
        self.assertNotContains(resp, '<img src=x onerror=alert(1)>')

    def test_limits_validate_inputs_and_prevent_flooding(self):
        self.grant()
        url = reverse("assistant_chat")
        for value in ("", "  ", "x" * 401):
            self.assertEqual(self.client.post(url, {"question": value}).status_code, 400)
        for _ in range(8):
            self.assertEqual(self.client.post(url, {"question": "статус"}).status_code, 302)
        self.assertEqual(self.client.post(url, {"question": "статус"}).status_code, 429)
        self.assertEqual(AssistantMessage.objects.filter(user=self.seller).count(), 8)

    def test_daily_limit_across_store_switches(self):
        self.grant()
        when = timezone.now() - timedelta(minutes=10)
        AssistantMessage.objects.bulk_create([
            AssistantMessage(user=self.seller, account=self.store, question="старый", answer="ответ", created_at=when)
            for _ in range(80)
        ])
        self.assertEqual(self.client.post(reverse("assistant_chat"), {"question": "привет"}).status_code, 429)

    def test_post_uses_no_wb_network_and_mutates_no_prices(self):
        self.grant()
        with patch("guard.views.WBClient") as wb, patch("guard.monitor.WBClient") as monitor:
            r = self.client.post(reverse("assistant_chat"), {"question": "Проверка статуса"})
        self.assertEqual(r.status_code, 302)
        wb.assert_not_called()
        monitor.assert_not_called()
        self.assertEqual(Product.objects.get(account=self.store, nm_id=112).current_discount, 35)

    def test_csrf_and_invalid_methods(self):
        self.grant()
        url = reverse("assistant_chat")
        self.assertEqual(self.client.put(url, data="x", content_type="text/plain").status_code, 405)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.seller)
        self.assertEqual(csrf.post(url, {"question": "status"}).status_code, 403)

    def test_does_not_claim_unsupported_wb_economics(self):
        self.grant()
        self.assertEqual(self.client.post(reverse("assistant_chat"), {
            "question": "Это скидка за счет WB прибыльна?"
        }).status_code, 302)
        answer = AssistantMessage.objects.get().answer
        self.assertIn("нельзя определить", answer)
        self.assertNotIn("гарантирован", answer)
