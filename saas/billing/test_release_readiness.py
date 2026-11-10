"""Release decisions must remain fail-closed despite optimistic local flags."""
import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import OwnerAISettings, OwnerAIGatewayConnection, OwnerTariffOffer
from .release_readiness import release_report
from guard.models import GuardAccount, Product


class ReleaseReadinessTests(TestCase):
    def test_no_users_or_proof_no_go_and_no_external_network(self):
        with patch("guard.wb.WBClient") as wb, patch("billing.tochka.get_tochka_operation") as bank:
            report = release_report()
        self.assertFalse(report["controlled_beta_technically_ready"])
        self.assertFalse(report["public_go"])
        self.assertGreaterEqual(len(report["external_evidence"]), 8)
        self.assertTrue(any(not check["ok"] for check in report["checks"]))
        wb.assert_not_called()
        bank.assert_not_called()

    @override_settings(
        DEBUG=False, SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True,
        SECURE_SSL_REDIRECT=True, SAAS_ACCESS_ENFORCED=True,
        SAAS_PIN_AUTH_ENABLED=True, REQUIRE_EMAIL_VERIFICATION=True,
        SAAS_WB_NETWORK_ENABLED=True, SAAS_WB_READ_ONLY=False,
        TOCHKA_BILLING_ENABLED=True, NOSALE_FISCALIZATION_CONFIRMED=True,
    )
    def test_all_local_flags_still_not_public_go(self):
        U = get_user_model()
        U.objects.create_superuser(username="owner@local.invalid", password="unit")
        seller = U.objects.create_user(username="seller@local.invalid", password="unit")
        store = GuardAccount.objects.create(
            user=seller, marketplace="wb", enabled=True,
            token_ciphertext="opaque-test-only",
            last_check_at=timezone.now(),
            last_error="",
        )
        Product.objects.create(account=store, nm_id=897, active=True)
        OwnerAISettings.objects.create(
            ai_enabled=True, gateway_model_id="deepseek/deepseek-flash",
            ai_platform_cost_cap_confirmed=True, monthly_budget_kopeks=100000,
            smart_briefs_per_day=2, web_assistant_enabled=False,
        )
        OwnerAIGatewayConnection.objects.create(
            encrypted_service_token="opaque-test-only", last_check_ok=True,
            catalog_models=["deepseek/deepseek-flash"],
        )
        for slug, price in (("standard", 59900), ("smart", 99000)):
            OwnerTariffOffer.objects.create(
                slug=slug, name=slug.title(), price_kopeks=price, enabled=True,
            )
        report = release_report()
        self.assertTrue(report["controlled_beta_technically_ready"])
        self.assertFalse(report["public_go"])
        self.assertEqual(report["decision"], "NO_GO_TECHNICAL_AND_EXTERNAL")
        self.assertEqual(
            [check["id"] for check in report["checks"] if not check["ok"]],
            ["admin_refund"],
        )
        self.assertIn("no free AI chat", report["scope"])

    @override_settings(
        SAAS_WB_NETWORK_ENABLED=True, SAAS_WB_READ_ONLY=False,
    )
    def test_stale_wb_inventory_is_not_readiness(self):
        user = get_user_model().objects.create_user("stale@local.invalid")
        store = GuardAccount.objects.create(
            user=user, marketplace="wb", enabled=True,
            token_ciphertext="opaque",
            last_check_at=timezone.now()-timedelta(days=3),
        )
        Product.objects.create(account=store, nm_id=991, active=True)
        report = release_report()
        wb = next(check for check in report["checks"] if check["id"] == "wb")
        self.assertFalse(wb["ok"])

    def test_command_json_and_strict_no_go_have_no_side_effects(self):
        before = GuardAccount.objects.count()
        output = StringIO()
        call_command("nosale_release_check", "--json", stdout=output)
        report = json.loads(output.getvalue())
        self.assertFalse(report["public_go"])
        self.assertEqual(GuardAccount.objects.count(), before)
        with self.assertRaises(CommandError):
            call_command("nosale_release_check", "--require-public-go", stdout=StringIO())
