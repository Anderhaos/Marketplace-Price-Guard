from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("push", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="pushmessage",
            name="smart_slot",
            field=models.CharField(blank=True, max_length=24, null=True),
        ),
        migrations.AlterField(
            model_name="pushmessage",
            name="kind",
            field=models.CharField(
                choices=[("event", "Event"), ("digest", "Digest"), ("smart", "Smart brief")],
                max_length=12,
            ),
        ),
        migrations.AddConstraint(
            model_name="pushmessage",
            constraint=models.UniqueConstraint(
                fields=("user", "smart_slot"),
                condition=Q(smart_slot__isnull=False),
                name="push_smart_slot_once",
            ),
        ),
    ]
