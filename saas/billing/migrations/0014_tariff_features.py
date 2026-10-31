from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("billing", "0013_smart_schedule_budget")]
    operations = [
        migrations.AddField(
            model_name="ownertariffoffer",
            name="features",
            field=models.JSONField(
                null=True, blank=True, default=None,
                help_text="Возможности действующих подписчиков тарифа. Не управляет оплатой или правами владельца.",
            ),
        ),
    ]
