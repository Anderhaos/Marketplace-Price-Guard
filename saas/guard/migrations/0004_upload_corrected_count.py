from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("guard", "0003_guardaccount_apply_all_requested")]

    operations = [
        migrations.AddField(
            model_name="upload",
            name="corrected_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
