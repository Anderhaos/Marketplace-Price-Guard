from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("guard", "0002_guardaccount_check_requested")]

    operations = [
        migrations.AddField(
            model_name="guardaccount",
            name="apply_all_requested",
            field=models.BooleanField(default=False),
        ),
    ]
