from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("billing", "0003_payment_last_reconciled")]

    operations = [
        migrations.AddField(
            model_name="accessgrant",
            name="plan",
            field=models.CharField(
                choices=[("standard", "Standard"), ("smart", "Smart")],
                default="standard", max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="subscriptionpayment",
            name="plan",
            field=models.CharField(
                choices=[("standard", "Standard"), ("smart", "Smart")],
                default="standard", max_length=16,
            ),
        ),
    ]
