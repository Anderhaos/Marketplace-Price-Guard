from django.db import migrations, models
import django.core.validators


def seed_owner_mcp(apps, schema_editor):
    # Preserve already tested read-only MCP behavior at migration time.
    # Owner may subsequently disable any tool immediately in /admin/.
    apps.get_model("billing", "OwnerMCPSettings").objects.using(schema_editor.connection.alias).get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [("billing", "0008_owner_web_assistant_controls")]

    operations = [
        migrations.CreateModel(
            name="OwnerMCPSettings",
            fields=[
                ("id", models.PositiveSmallIntegerField(editable=False, primary_key=True, serialize=False)),
                ("enabled", models.BooleanField(default=True, help_text="Общий переключатель внутренних MCP-инструментов. Выключение не останавливает прямую защиту скидок WB.")),
                ("wb_enabled", models.BooleanField(default=True, help_text="Разрешить чтение сохранённых данных WB через MCP.")),
                ("wb_overview_enabled", models.BooleanField(default=True, verbose_name="WB: сводка магазина")),
                ("wb_products_enabled", models.BooleanField(default=True, verbose_name="WB: товары и скидки")),
                ("wb_events_enabled", models.BooleanField(default=True, verbose_name="WB: события")),
                ("wb_promotions_enabled", models.BooleanField(default=True, verbose_name="WB: акции")),
                ("ozon_status_enabled", models.BooleanField(default=True, help_text="Пока API Ozon не подключён, инструмент сообщает только статус not_implemented.", verbose_name="Ozon: только состояние адаптера")),
                ("max_products", models.PositiveSmallIntegerField(default=20, help_text="Максимум товаров, которые Smart может запросить за один MCP-вызов (1–20).", validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(20)])),
                ("max_events", models.PositiveSmallIntegerField(default=20, help_text="Максимум событий в одном MCP-вызове (1–20).", validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(20)])),
                ("max_promotions", models.PositiveSmallIntegerField(default=15, help_text="Максимум акций в одном MCP-вызове (1–15).", validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(15)])),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Инструменты MCP владельца",
                "verbose_name_plural": "Инструменты MCP владельца",
            },
        ),
        migrations.RunPython(seed_owner_mcp, migrations.RunPython.noop),
    ]
