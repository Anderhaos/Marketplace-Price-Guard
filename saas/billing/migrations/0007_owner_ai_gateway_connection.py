from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("billing", "0006_owner_tariff_offers")]

    operations = [
        migrations.CreateModel(
            name="OwnerAIGatewayConnection",
            fields=[
                ("id", models.PositiveSmallIntegerField(editable=False, primary_key=True, serialize=False)),
                ("base_url", models.URLField(default="https://platform.svdev.pro/v1", max_length=255)),
                ("catalog_mode", models.CharField(choices=[("curated", "Curated"), ("scoped", "Chat only"), ("full", "Full")], default="scoped", max_length=12)),
                ("encrypted_service_token", models.TextField(blank=True, editable=False)),
                ("catalog_models", models.JSONField(blank=True, default=list, editable=False)),
                ("last_checked_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("last_check_ok", models.BooleanField(blank=True, editable=False, null=True)),
                ("last_check_message", models.CharField(blank=True, editable=False, max_length=220)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "Подключение AI Platform", "verbose_name_plural": "Подключение AI Platform"},
        ),
    ]
