"""NoSale uses the same owner-managed AI Platform connection pattern as MiniBook."""
import json
import os
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from billing.ai_platform_connection import (
    PlatformConnectionError, fetch_platform_models, normalize_base_url,
    runtime_gateway, verify_gateway,
)
from billing.models import OwnerAIGatewayConnection, OwnerAISettings
from guard.ai_gateway import gateway_chat


@override_settings(
    STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
              "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
    NOSALE_AI_GATEWAY_URL="", NOSALE_AI_SERVICE_TOKEN="",
)
class OwnerGatewayConnectionTests(TestCase):
    def setUp(self):
        self.keypatch = patch.dict(os.environ, {"TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii")})
        self.keypatch.start()
        self.addCleanup(self.keypatch.stop)
        self.owner = User.objects.create_superuser("owner-gw@example.test", "owner-gw@example.test", "password-123")
        self.staff = User.objects.create_user("staff-gw@example.test", "staff-gw@example.test", "password-123", is_staff=True)

    def add_connection(self, token="test-only-service-key-123456789"):
        self.client.force_login(self.owner)
        page = reverse("admin:billing_owneraigatewayconnection_add")
        response = self.client.post(page, {
            "base_url": "https://platform.svdev.pro/v1/",
            "catalog_mode": "scoped",
            "service_token": token,
            "_save": "Save",
        })
        self.assertEqual(response.status_code, 302)
        return OwnerAIGatewayConnection.objects.get()

    def test_owner_can_store_key_encrypted_and_never_display_plaintext(self):
        connection = self.add_connection()
        self.assertTrue(connection.configured)
        self.assertNotIn("test-only-service-key", connection.encrypted_service_token)
        self.assertEqual(connection.base_url, "https://platform.svdev.pro/v1")
        self.assertEqual(runtime_gateway(), (connection.base_url, "test-only-service-key-123456789"))
        url = reverse("admin:billing_owneraigatewayconnection_change", args=[connection.pk])
        html = self.client.get(url).content.decode()
        self.assertNotIn("test-only-service-key", html)
        self.assertNotIn(connection.encrypted_service_token, html)
        self.assertIn("Сохранён (зашифрован)", html)
        # Blank change preserves previously encrypted key.
        result = self.client.post(url, {
            "base_url": "https://platform.svdev.pro/v1", "catalog_mode": "curated",
            "service_token": "", "_save": "Save",
        })
        self.assertEqual(result.status_code, 302)
        connection.refresh_from_db()
        self.assertEqual(connection.catalog_mode, "curated")
        self.assertEqual(runtime_gateway()[1], "test-only-service-key-123456789")

    def test_superuser_only_no_delete_or_second_connection(self):
        self.client.force_login(self.staff)
        for route in ("changelist", "add"):
            self.assertEqual(self.client.get(reverse("admin:billing_owneraigatewayconnection_"+route)).status_code, 403)
        connection = self.add_connection()
        self.assertEqual(self.client.get(reverse("admin:billing_owneraigatewayconnection_add")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:billing_owneraigatewayconnection_delete", args=[1])).status_code, 403)
        self.assertEqual(OwnerAIGatewayConnection.objects.count(), 1)

    def test_host_is_exact_not_every_svdev_subdomain(self):
        for url in (
            "http://platform.svdev.pro/v1",
            "https://evil.svdev.pro/v1",
            "https://platform.svdev.pro.evil.example/v1",
            "https://platform.svdev.pro/v1?x=1",
            "https://platform.svdev.pro:9443/v1",
            "https://admin:pass@platform.svdev.pro/v1",
        ):
            with self.subTest(url=url):
                with self.assertRaises(PlatformConnectionError):
                    normalize_base_url(url)
        self.assertEqual(normalize_base_url("https://platform.svdev.pro/v1/"),
                         "https://platform.svdev.pro/v1")

    def test_verification_requests_real_platform_catalog_and_excludes_nonchat(self):
        conn = self.add_connection()
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _): return json.dumps({"data": [
                {"id": "deepseek/deepseek-chat", "category": "chat", "selectable": True,
                 "execution": {"supported": True, "endpoints": ["/v1/chat/completions"]}},
                {"id": "openai/gpt-image", "category": "image", "selectable": True,
                 "execution": {"supported": True, "endpoints": ["/v1/images"]}},
                {"id": "hidden/unavailable", "category": "chat", "selectable": False},
            ]}).encode()
        class Opener:
            def open(self, request, timeout):
                self.request, self.timeout = request, timeout
                return Response()
        opener = Opener()
        with patch("billing.ai_platform_connection.build_opener", return_value=opener):
            self.assertEqual(verify_gateway(conn), ["deepseek/deepseek-chat"])
        self.assertEqual(opener.request.full_url, "https://platform.svdev.pro/v1/models?category=chat")
        self.assertEqual(opener.request.get_header("X-ai-project-key"), "nosale")
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer test-only-service-key-123456789")
        conn.refresh_from_db()
        self.assertTrue(conn.last_check_ok)
        self.assertEqual(conn.catalog_models, ["deepseek/deepseek-chat"])
        # Admin selector derives from the verified platform catalog, not a hand-coded provider list.
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("admin:billing_owneraisettings_add")), "deepseek/deepseek-chat")

    def test_invalid_token_is_not_accepted_or_leaked(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("admin:billing_owneraigatewayconnection_add"), {
            "base_url": "https://platform.svdev.pro/v1",
            "catalog_mode": "scoped", "service_token": "too-short", "_save": "Save",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(OwnerAIGatewayConnection.objects.exists())

    def test_inference_uses_encrypted_connection_not_browser_token(self):
        self.add_connection()
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _): return b'{"choices":[{"message":{"content":"Проверьте товар"}}]}'
        class Opener:
            def open(self, request, timeout):
                self.request, self.timeout = request, timeout
                return Response()
        opener = Opener()
        with patch("guard.ai_gateway.build_opener", return_value=opener):
            answer, usage = gateway_chat(model_id="deepseek/deepseek-chat",
                messages=[{"role": "user", "content": "Что проверить?"}],
                max_output_tokens=120, timeout_seconds=5)
        self.assertEqual(answer, "Проверьте товар")
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer test-only-service-key-123456789")
        self.assertEqual(opener.request.get_header("X-ai-project-key"), "nosale")
        self.assertEqual(opener.request.full_url, "https://platform.svdev.pro/v1/chat/completions")

    def test_env_override_remains_emergency_only(self):
        self.add_connection()
        with override_settings(NOSALE_AI_GATEWAY_URL="https://platform.svdev.pro/v1/chat/completions",
                               NOSALE_AI_SERVICE_TOKEN="emergency-override-token"):
            self.assertEqual(runtime_gateway()[1], "emergency-override-token")
        with override_settings(NOSALE_AI_GATEWAY_URL="https://evil.svdev.pro/v1/chat/completions",
                               NOSALE_AI_SERVICE_TOKEN="emergency-override-token"):
            self.assertIsNone(runtime_gateway())

    def test_admin_action_verifies_only_on_explicit_post(self):
        conn = self.add_connection()
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _):
                return b'{"data":[{"id":"deepseek/deepseek-chat","category":"chat","selectable":true,"execution":{"supported":true,"endpoints":["/v1/chat/completions"]}}]}'
        class Opener:
            def open(self, request, timeout): return Response()
        with patch("billing.ai_platform_connection.build_opener", return_value=Opener()) as opener:
            result = self.client.post(reverse("admin:billing_owneraigatewayconnection_changelist"), {
                "action": "verify_selected", "_selected_action": [str(conn.pk)], "index": "0",
            })
        self.assertEqual(result.status_code, 302)
        opener.assert_called_once()
        conn.refresh_from_db()
        self.assertTrue(conn.last_check_ok)
