from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [("billing", "0012_verified_tool_models")]
    operations = [
        migrations.AlterField(model_name="owneraisettings", name="web_assistant_enabled",
            field=models.BooleanField(default=False, help_text="Устаревший ручной чат выключен; AI работает только для плановых сводок.")),
        migrations.AddField(model_name="owneraisettings", name="smart_morning_hour",
            field=models.PositiveSmallIntegerField(default=8, validators=[django.core.validators.MaxValueValidator(23)], help_text="Утренняя сводка по местному времени пользователя.")),
        migrations.AddField(model_name="owneraisettings", name="smart_evening_hour",
            field=models.PositiveSmallIntegerField(default=20, validators=[django.core.validators.MaxValueValidator(23)], help_text="Вечерняя сводка по местному времени пользователя.")),
        migrations.AddField(model_name="owneraisettings", name="smart_reserve_kopeks",
            field=models.PositiveIntegerField(default=100, validators=[django.core.validators.MinValueValidator(1)], help_text="Резерв на один DeepSeek-ответ в копейках; не фактическая стоимость модели.")),
        migrations.AddField(model_name="owneraisettings", name="ai_platform_cost_cap_confirmed",
            field=models.BooleanField(default=False, help_text="Реальный отдельный лимит расходов подтверждён в AI Platform.")),
    ]
