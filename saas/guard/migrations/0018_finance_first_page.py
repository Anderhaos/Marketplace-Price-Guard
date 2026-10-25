# NoSale R5.2: only sanitized, seller-owned first-page finance evidence.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("guard", "0017_financerategate")]

    operations = [
        migrations.AddField(model_name="financesnapshot", name="detail_report_id",
                            field=models.PositiveBigIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="financesnapshot", name="detail_rows",
                            field=models.JSONField(default=list)),
        migrations.AddField(model_name="financesnapshot", name="detail_fetched_at",
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="financesnapshot", name="detail_partial",
                            field=models.BooleanField(default=True)),
    ]
