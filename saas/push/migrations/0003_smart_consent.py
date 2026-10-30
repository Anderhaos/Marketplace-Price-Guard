from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("push", "0002_smart_brief_slot")]
    operations = [
        migrations.AddField(model_name="notificationpreference", name="smart_ai_consent_at",
            field=models.DateTimeField(null=True, blank=True)),
    ]
