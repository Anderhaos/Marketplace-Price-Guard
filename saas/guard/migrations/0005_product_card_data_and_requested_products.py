from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("guard", "0004_upload_corrected_count")]

    operations = [
        migrations.AddField(model_name="guardaccount", name="apply_requested_ids", field=models.JSONField(default=list)),
        migrations.AddField(model_name="product", name="photo_url", field=models.URLField(blank=True)),
        migrations.AddField(model_name="product", name="title", field=models.CharField(blank=True, max_length=500)),
    ]
