from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("guard", "0011_assistant_message"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(model_name="assistantmessage", name="mode", field=models.CharField(choices=[("basic", "Базовый"), ("smart", "AI Smart")], default="basic", max_length=8)),
        migrations.CreateModel(
            name="AssistantAIRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("status", models.CharField(choices=[("reserved", "Reserved"), ("success", "Success"), ("failed", "Failed")], default="reserved", max_length=12)),
                ("model_id", models.CharField(max_length=120)),
                ("reserved_kopeks", models.PositiveIntegerField(default=0)),
                ("input_tokens", models.PositiveIntegerField(default=0)),
                ("output_tokens", models.PositiveIntegerField(default=0)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ai_requests", to="guard.guardaccount")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="nosale_ai_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={"indexes": [models.Index(fields=["user", "created_at"], name="guard_ai_req_user_time_idx")]},
        ),
    ]
