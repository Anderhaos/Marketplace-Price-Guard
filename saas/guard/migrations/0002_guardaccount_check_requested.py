from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("guard", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="guardaccount",
            name="check_requested",
            field=models.BooleanField(default=False),
        ),
    ]
