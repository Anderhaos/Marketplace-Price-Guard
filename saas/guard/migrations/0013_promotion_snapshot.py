from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("guard", "0012_assistant_ai_request")]

    operations = [
        migrations.CreateModel(
            name="PromotionSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("promotions", models.JSONField(default=list)),
                ("partial", models.BooleanField(default=True)),
                ("fetched_at", models.DateTimeField(blank=True, null=True)),
                ("next_refresh_at", models.DateTimeField(blank=True, null=True)),
                ("locked_until", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, max_length=220)),
                ("account", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="promotion_snapshot", to="guard.guardaccount")),
            ],
        ),
    ]
