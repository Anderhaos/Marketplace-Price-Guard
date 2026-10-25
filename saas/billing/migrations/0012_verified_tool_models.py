from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("billing", "0011_owner_mcp_adapter_registry")]

    operations = [
        migrations.AddField(
            model_name="owneraigatewayconnection",
            name="catalog_tool_models",
            field=models.JSONField(blank=True, default=list, editable=False),
        ),
    ]
