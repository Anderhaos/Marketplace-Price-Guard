"""R3 safe owner config: only a superuser may configure provider/model/quotas."""
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import OwnerAISettings


@override_settings(STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class OwnerAISettingsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="owner@example.test", email="owner@example.test", password="test-only-pass"
        )
        self.staff = User.objects.create_user(
            username="staff@example.test", email="staff@example.test", password="test-only-pass",
            is_staff=True,
        )

    def test_off_by_default_and_no_provider_credentials_stored(self):
        obj = OwnerAISettings.objects.create()
        self.assertFalse(obj.ai_enabled)
        self.assertFalse(obj.ready_for_gateway)
        fields = [f.name for f in OwnerAISettings._meta.fields]
        self.assertFalse(any(field.endswith("_token") or "secret" in field or "api_key" in field for field in fields))
        obj.ai_enabled = True
        obj.gateway_model_id = "approved-example-model"
        obj.save()
        self.assertFalse(obj.ready_for_gateway)
        obj.monthly_budget_kopeks = 10000
        obj.save()
        self.assertTrue(obj.ready_for_gateway)

    def test_settings_singleton(self):
        first = OwnerAISettings.objects.create()
        second = OwnerAISettings()
        second.save()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(OwnerAISettings.objects.count(), 1)

    def test_owner_only_admin_and_no_deletion(self):
        self.client.force_login(self.staff)
        changelist = reverse("admin:billing_owneraisettings_changelist")
        self.assertEqual(self.client.get(changelist).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(changelist).status_code, 200)
        add = reverse("admin:billing_owneraisettings_add")
        self.assertEqual(self.client.get(add).status_code, 200)
        # Settings without runtime implementation must not be advertised.
        form_page = self.client.get(add)
        for unused in ("standard_questions_per_day", "prompt_version"):
            self.assertNotContains(form_page, f'name="{unused}"')
        self.assertContains(form_page, 'name="smart_briefs_per_day"')
        self.client.post(add, {
            "web_assistant_enabled": "on",
            "web_messages_per_day": "12",
            "ai_enabled": "on",
            "gateway_model_id": "published-model-id",
            "prompt_version": "pretend-enabled",
            "system_instructions": "Read-only",
            "standard_questions_per_day": "999",
            "smart_questions_per_day": "2",
            "smart_briefs_per_day": "2",
            "max_output_tokens": "500",
            "timeout_seconds": "20",
            "monthly_budget_kopeks": "10000",
            "_save": "Save",
        })
        item = OwnerAISettings.objects.get()
        self.assertTrue(item.ready_for_gateway)
        self.assertTrue(item.web_assistant_enabled)
        self.assertEqual(item.web_messages_per_day, 12)
        self.assertEqual(item.gateway_model_id, "published-model-id")
        # Forged POST values cannot mutate legacy nonfunctional controls.
        self.assertEqual(item.standard_questions_per_day, 1)
        self.assertEqual(item.smart_briefs_per_day, 2)
        self.assertEqual(item.prompt_version, "v1")
        self.assertEqual(self.client.get(add).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("admin:billing_owneraisettings_delete", args=[item.pk])).status_code,
            403,
        )
