from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("guard", "0010_initial_sync_completed_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AssistantMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("question", models.CharField(max_length=400)),
                ("answer", models.TextField(max_length=2000)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="assistant_messages", to="guard.guardaccount")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="nosale_assistant_messages", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [models.Index(fields=["user", "account", "created_at"], name="guard_assis_user_id_593b14_idx")],
            },
        ),
    ]
