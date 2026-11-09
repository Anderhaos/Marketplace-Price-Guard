from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("guard", "0019_ai_source"),
    ]
    operations = [
        migrations.AddField(
            model_name="guardaccount", name="basic_staged_changes",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="guardaccount", name="basic_staged_rules_version",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
