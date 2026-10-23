from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("billing", "0005_owner_ai_settings")]

    operations = [
        migrations.CreateModel(
            name="OwnerTariffOffer",
            fields=[
                ("slug", models.CharField(choices=[("standard", "Standard"), ("smart", "Smart")], max_length=16, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=50)),
                ("price_kopeks", models.PositiveIntegerField(help_text="Цена за один месяц в копейках. Проверяется на сервере.")),
                ("enabled", models.BooleanField(default=False, help_text="При выключении новые покупки этого тарифа запрещены.")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "Тариф для продажи", "verbose_name_plural": "Тарифы для продажи", "ordering": ["slug"]},
        ),
    ]
