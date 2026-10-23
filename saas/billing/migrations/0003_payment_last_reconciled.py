from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("billing", "0002_acquiring_payment_link")]
    operations = [migrations.AddField(model_name="subscriptionpayment", name="last_reconciled_at", field=models.DateTimeField(blank=True, null=True))]
