from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("guard", "0018_finance_first_page")]
    operations = [
        migrations.AddField(model_name="assistantairequest", name="source",
            field=models.CharField(default="manual", max_length=12,
                choices=[("manual", "Legacy manual"), ("scheduled", "Scheduled brief")])),
    ]
