from django.db import migrations, models
from django.db.models import F


def backfill_completed_sync(apps, schema_editor):
    """Keep established stores open only if an earlier scan succeeded."""
    Account = apps.get_model("guard", "GuardAccount")
    Account.objects.using(schema_editor.connection.alias).filter(
        token_ciphertext__gt="", last_check_at__isnull=False, last_error="",
    ).update(initial_sync_completed_at=F("last_check_at"))


class Migration(migrations.Migration):
    dependencies = [("guard", "0009_pin_lock_escalation")]
    operations = [
        migrations.AddField(
            model_name="guardaccount", name="initial_sync_completed_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.RunPython(backfill_completed_sync, migrations.RunPython.noop),
    ]
