from datetime import timedelta
"""Owner R4.1: integration status is local-only, owner-scoped, and audited."""
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import OwnerAISettings, OwnerAIGatewayConnection, OwnerMCPSettings
from .owner_readiness import integration_readiness


@override_settings(
    SAAS_PIN_AUTH_ENABLED=True,
    REQUIRE_EMAIL_VERIFICATION=True,
    SAAS_WB_NETWORK_ENABLED=False,
    SAAS_WB_READ_ONLY=True,
    TOCHKA_BILLING_ENABLED=False,
    NOSALE_FISCALIZATION_CONFIRMED=False,
    EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class OwnerIntegrationReadinessTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="readiness-owner@example.test",
            email="readiness-owner@example.test", password="r6-only-testing",
        )
        self.staff = User.objects.create_user(
            username="readiness-staff@example.test", password="test-only",
            is_staff=True,
        )
        self.seller = User.objects.create_user(
            username="readiness-seller@example.test", password="test-only",
        )
        self.url = reverse("nosale_owner_integrations")
        self.assertEqual(self.url, "/admin/integrations/")

    def login_verified_owner_for_unrelated_admin_tests(self):
        # These tests cover integration-readiness and audit, not code delivery.
        self.client.force_login(self.owner)
        session = self.client.session
        session["nosale_owner_admin_verified"] = {
            "user_id": self.owner.pk,
            "valid_until": (timezone.now() + timedelta(minutes=10)).timestamp(),
        }
        session.save()

    def test_owner_page_displays_true_local_status_no_provider_calls(self):
        OwnerAISettings.objects.create(
            ai_enabled=True, gateway_model_id="approved-chat",
            monthly_budget_kopeks=20000,
        )
        gateway = OwnerAIGatewayConnection.objects.create(
            encrypted_service_token="SECRET_DO_NOT_RENDER_THIS",
            last_checked_at=timezone.now(), last_check_ok=True,
            catalog_models=["approved-chat"],
        )
        self.login_verified_owner_for_unrelated_admin_tests()
        with patch("billing.ai_platform_connection.build_opener") as net, \
             patch("guard.crypto.decrypt_token") as dec, \
             patch("guard.wb.urlopen") as wb_net:
            response = self.client.get(self.url)
            net.assert_not_called()
            dec.assert_not_called()
            wb_net.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wildberries")
        self.assertContains(response, "Ozon")
        self.assertContains(response, "Не реализован")
        self.assertContains(response, "approved-chat")
        self.assertContains(response, "AI Platform")
        self.assertContains(response, "Отключено")
        self.assertNotContains(response, "SECRET_DO_NOT_RENDER_THIS")
        self.assertEqual(gateway.encrypted_service_token, "SECRET_DO_NOT_RENDER_THIS")
        self.assertFalse(integration_readiness()["payments"]["commercial_ready"])

    def test_unverified_connection_does_not_claim_live_ai(self):
        OwnerAISettings.objects.create(
            ai_enabled=True, gateway_model_id="missing-model",
            monthly_budget_kopeks=99999,
        )
        OwnerAIGatewayConnection.objects.create(
            encrypted_service_token="never_display",
            last_check_ok=False, catalog_models=["missing-model"],
        )
        self.login_verified_owner_for_unrelated_admin_tests()
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Не готов к запросам")
        self.assertNotContains(r, "never_display")
        self.assertFalse(integration_readiness()["ai"]["usable"])

    def test_all_nonowner_roles_refused_including_direct_audit_url(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        audit = reverse("admin:admin_logentry_changelist")
        for user in (self.seller, self.staff):
            self.client.force_login(user)
            for url in (self.url, audit, reverse("admin:index")):
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)

    def test_post_edit_is_real_mcp_setting_and_generates_readonly_audit(self):
        self.login_verified_owner_for_unrelated_admin_tests()
        change = reverse("admin:billing_ownermcpsettings_change", args=[1])
        result = self.client.post(change, {
            "enabled": "on", "wb_enabled": "on",
            "wb_overview_enabled": "on", "wb_events_enabled": "on",
            "wb_promotions_enabled": "on", "ozon_status_enabled": "on",
            # Intentionally omit wb_products_enabled: turn OFF on server.
            "max_products": "2", "max_events": "3",
            "max_promotions": "3", "_save": "Сохранить",
        })
        self.assertEqual(result.status_code, 302)
        policy = OwnerMCPSettings.runtime()
        self.assertFalse(policy.wb_products_enabled)
        self.assertEqual(policy.max_events, 3)
        entry = LogEntry.objects.filter(
            user=self.owner,
            content_type__app_label="billing",
            content_type__model="ownermcpsettings",
            action_flag=2,
        ).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.object_id, "1")
        self.assertNotIn("SECRET", entry.change_message)
        audit_list = self.client.get(reverse("admin:admin_logentry_changelist"))
        self.assertEqual(audit_list.status_code, 200)
        self.assertContains(audit_list, "NoSale")
        self.assertContains(audit_list, self.owner.username)
        self.assertEqual(
            self.client.get(reverse("admin:admin_logentry_delete", args=[entry.pk])).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse("admin:admin_logentry_add")).status_code,
            403,
        )

    def test_does_not_leak_other_models_in_owner_config_audit(self):
        self.login_verified_owner_for_unrelated_admin_tests()
        from django.contrib.admin.models import ADDITION
        from django.contrib.contenttypes.models import ContentType
        LogEntry.objects.log_actions(
            user_id=self.owner.pk,
            queryset=User.objects.filter(pk=self.seller.pk),
            action_flag=ADDITION,
            change_message="seller record",
            single_object=False,
        )
        listing = self.client.get(reverse("admin:admin_logentry_changelist"))
        self.assertEqual(listing.status_code, 200)
        self.assertNotContains(listing, "seller record")
