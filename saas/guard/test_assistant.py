"""NoSale seller AI is scheduled; the historical manual chat is retired."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import AccessGrant, OwnerAISettings
from guard.models import AssistantAIRequest, AssistantMessage, GuardAccount


@override_settings(SAAS_PIN_AUTH_ENABLED=False, SAAS_ACCESS_ENFORCED=True)
class RetiredChatTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user("retired-chat@example.test", password="test")
        GuardAccount.objects.create(user=self.seller, marketplace="wb", name="WB")
        AccessGrant.objects.create(
            user=self.seller, plan="smart", source="admin", source_ref="retired-chat",
            starts_at=timezone.now()-timedelta(days=1),
            ends_at=timezone.now()+timedelta(days=1),
        )

    def test_old_get_redirects_to_saved_recommendations(self):
        url = reverse("assistant_chat")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.seller)
        r = self.client.get(url)
        self.assertRedirects(r, reverse("notifications"), fetch_redirect_response=False)

    def test_old_post_never_calls_ai_or_saves_prompt(self):
        self.client.force_login(self.seller)
        OwnerAISettings.objects.create(web_assistant_enabled=True, ai_enabled=True)
        for mode in ("basic", "smart"):
            with self.subTest(mode=mode), patch("guard.smart_assistant.gateway_chat") as model:
                r = self.client.post(reverse("assistant_chat"), {
                    "mode": mode, "question": "Покажи скидки",
                    "ai_consent": "yes", "mcp_consent": "yes",
                })
                self.assertEqual(r.status_code, 405)
                model.assert_not_called()
        self.assertEqual(AssistantMessage.objects.count(), 0)
        self.assertEqual(AssistantAIRequest.objects.count(), 0)
