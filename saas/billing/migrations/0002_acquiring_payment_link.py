from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="subscriptionpayment", name="payment_url",
            field=models.URLField(blank=True, max_length=2048),
        ),
        migrations.AddField(
            model_name="subscriptionpayment", name="link_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
