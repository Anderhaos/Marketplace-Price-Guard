from django.db import migrations, models


def seed_reviewed_adapters(apps, schema_editor):
    Adapter = apps.get_model("billing", "OwnerMCPAdapter")
    db = schema_editor.connection.alias
    # Defaults preserve R1/R2 behavior: WB cached reads and Ozon honest
    # not_implemented status may be requested. Neither is a live Ozon API.
    for slug, implementation in (
        ("wb", "read_only_cached"),
        ("ozon", "status_only_unimplemented"),
    ):
        Adapter.objects.using(db).get_or_create(
            slug=slug, defaults={"enabled": True, "implementation": implementation},
        )


class Migration(migrations.Migration):
    dependencies = [("billing", "0010_owner_operations_settings")]

    operations = [
        migrations.CreateModel(
            name="OwnerMCPAdapter",
            fields=[
                ("slug", models.CharField(
                    choices=[("wb", "Wildberries"), ("ozon", "Ozon")],
                    editable=False, max_length=16, primary_key=True, serialize=False,
                )),
                ("enabled", models.BooleanField(
                    default=True, verbose_name="Разрешён владельцем",
                    help_text="Отключение запрещает вызовы всех MCP-инструментов этой площадки. Защита WB через прямой API и другие площадки продолжают работать.",
                )),
                ("implementation", models.CharField(
                    choices=[
                        ("read_only_cached", "Встроен: чтение сохранённых данных"),
                        ("status_only_unimplemented", "Только статус: API не подключён"),
                    ],
                    editable=False, max_length=32,
                )),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Адаптер MCP маркетплейса",
                "verbose_name_plural": "Адаптеры MCP маркетплейсов",
            },
        ),
        migrations.RunPython(seed_reviewed_adapters, migrations.RunPython.noop),
    ]
