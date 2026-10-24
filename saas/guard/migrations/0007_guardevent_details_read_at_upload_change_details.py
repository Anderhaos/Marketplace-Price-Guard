from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("guard", "0006_guardaccount_marketplace_guardaccount_name_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="guardevent",
            name="details",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="guardevent",
            name="read_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="upload",
            name="change_details",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
