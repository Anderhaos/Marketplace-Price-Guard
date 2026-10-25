"""Verified AI Platform tool metadata must gate Smart MCP before any provider IO."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from billing.ai_platform_connection import is_verified_tool_model
from billing.models import AccessGrant, OwnerAISettings, OwnerAIGatewayConnection
from guard.ai_gateway import AIGatewayUnavailable
from guard.models import AssistantAIRequest, GuardAccount
from guard.smart_assistant import smart_store_reply


@override_settings(NOSALE_AI_GATEWAY_URL="", NOSALE_AI_SERVICE_TOKEN="")
class VerifiedToolModelTests(TestCase):
    def setUp(self):
        self.connection = OwnerAIGatewayConnection.objects.create(
            last_check_ok=True,
            catalog_models=["chat/with-tools", "chat/no-tools"],
            catalog_tool_models=["chat/with-tools"],
        )

    def test_only_explicit_verified_catalog_model_is_tool_capable(self):
        self.assertTrue(is_verified_tool_model("chat/with-tools"))
        self.assertFalse(is_verified_tool_model("chat/no-tools"))
        self.assertFalse(is_verified_tool_model("chat/not-authorized"))
        self.connection.last_check_ok = False
        self.connection.save(update_fields=["last_check_ok"])
        self.assertFalse(is_verified_tool_model("chat/with-tools"))

    def test_old_catalog_without_capability_metadata_fails_closed(self):
        self.connection.catalog_tool_models = []
        self.connection.save(update_fields=["catalog_tool_models"])
        self.assertFalse(is_verified_tool_model("chat/with-tools"))

    def test_smart_mcp_fails_before_quota_or_provider_when_model_lacks_tools(self):
        seller = User.objects.create_user("seller-capability@example.test", password="pass")
        store = GuardAccount.objects.create(user=seller, name="Test only")
        now = timezone.now()
        AccessGrant.objects.create(
            user=seller, source="admin", source_ref="capability-test",
            plan="smart", starts_at=now - timedelta(minutes=1),
            ends_at=now + timedelta(days=1),
        )
        OwnerAISettings.objects.create(
            ai_enabled=True, gateway_model_id="chat/no-tools",
            smart_questions_per_day=3, monthly_budget_kopeks=2000,
        )
        with patch("guard.smart_assistant.is_gateway_configured", return_value=True), \
             patch("guard.smart_assistant.gateway_choose_tool") as chooser, \
             patch("guard.smart_assistant.gateway_chat") as chat:
            with self.assertRaisesMessage(AIGatewayUnavailable, "not verified for MCP tools"):
                smart_store_reply(seller.pk, store, "What happened?", use_mcp=True)
            chooser.assert_not_called()
            chat.assert_not_called()
        self.assertFalse(AssistantAIRequest.objects.exists())
