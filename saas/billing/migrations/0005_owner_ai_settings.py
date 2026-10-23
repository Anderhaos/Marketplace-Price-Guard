from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("billing", "0004_subscription_plans")]

    operations = [
        migrations.CreateModel(
            name="OwnerAISettings",
            fields=[
                ("id", models.PositiveSmallIntegerField(editable=False, primary_key=True, serialize=False)),
                ("ai_enabled", models.BooleanField(default=False, help_text="Выключает AI, но не контроль скидок.")),
                ("gateway_model_id", models.CharField(blank=True, help_text="Точный опубликованный идентификатор модели в AI Platform.", max_length=120)),
                ("prompt_version", models.CharField(default="v1", max_length=32)),
                ("system_instructions", models.TextField(blank=True, help_text="Дополнительные инструкции ассистенту. Запрещено давать право записи в WB.")),
                ("standard_questions_per_day", models.PositiveSmallIntegerField(default=1)),
                ("smart_questions_per_day", models.PositiveSmallIntegerField(default=2)),
                ("smart_briefs_per_day", models.PositiveSmallIntegerField(default=2)),
                ("max_output_tokens", models.PositiveSmallIntegerField(default=500)),
                ("timeout_seconds", models.PositiveSmallIntegerField(default=20)),
                ("monthly_budget_kopeks", models.PositiveIntegerField(default=0, help_text="0 = AI вызовы запрещены даже при включённом переключателе.")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Настройки AI владельца",
                "verbose_name_plural": "Настройки AI владельца",
            },
        ),
    ]
