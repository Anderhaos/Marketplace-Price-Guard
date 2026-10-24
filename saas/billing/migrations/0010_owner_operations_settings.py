from django.db import migrations, models
import django.core.validators


def seed_owner_operations(apps, schema_editor):
    apps.get_model("billing", "OwnerOperationsSettings").objects.using(
        schema_editor.connection.alias
    ).get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [("billing", "0009_owner_mcp_settings")]

    operations = [
        migrations.CreateModel(
            name="OwnerOperationsSettings",
            fields=[
                ("id", models.PositiveSmallIntegerField(editable=False, primary_key=True, serialize=False)),
                ("guard_checks_enabled", models.BooleanField(default=True, help_text="Опасно отключать: остановятся новые циклы контроля цен/скидок всех продавцов. Уже начатый запрос может завершиться.", verbose_name="Новые фоновые проверки WB")),
                ("instant_events_enabled", models.BooleanField(default=True, help_text="Если отключено, события остаются в журнале, но новые push-доставки для них не создаются.", verbose_name="Мгновенные события в Push")),
                ("daily_digests_enabled", models.BooleanField(default=True, help_text="Отключает создание новых ежедневных сводок. Не стирает уже созданные.", verbose_name="Ежедневные сводки Push")),
                ("push_delivery_enabled", models.BooleanField(default=True, help_text="При паузе ожидающие доставки сохраняются для дальнейшей отправки.", verbose_name="Отправка Web Push")),
                ("guard_poll_seconds", models.PositiveSmallIntegerField(default=10, validators=[django.core.validators.MinValueValidator(5), django.core.validators.MaxValueValidator(60)], verbose_name="Интервал опроса Guard, секунд (5–60)")),
                ("notifications_poll_seconds", models.PositiveSmallIntegerField(default=30, validators=[django.core.validators.MinValueValidator(15), django.core.validators.MaxValueValidator(300)], verbose_name="Интервал опроса Push, секунд (15–300)")),
                ("push_batch_size", models.PositiveSmallIntegerField(default=20, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(20)], verbose_name="Максимум Push за цикл (1–20)")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "Фоновые процессы SaaS", "verbose_name_plural": "Фоновые процессы SaaS"},
        ),
        migrations.RunPython(seed_owner_operations, migrations.RunPython.noop),
    ]
