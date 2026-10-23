from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("guard", "0008_email_pin_auth")]
    operations = [migrations.AddField(model_name="emailpinloginthrottle", name="lock_count", field=models.PositiveSmallIntegerField(default=0))]
