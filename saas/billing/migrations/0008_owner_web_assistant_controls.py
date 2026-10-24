"""Owner-managed on/off and quota for the embedded Standard/Smart web chat.

No changes to access grants, banking, WB guard rules or AI credentials.
Existing installations keep their previous 80-message rolling daily cap.
"""
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("billing", "0007_owner_ai_gateway_connection")]

    operations = [
        migrations.AddField(
            model_name="owneraisettings", name="web_assistant_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Включает встроенный чат Standard и Smart; не влияет на защиту WB.",
            ),
        ),
        migrations.AddField(
            model_name="owneraisettings", name="web_messages_per_day",
            field=models.PositiveSmallIntegerField(
                default=80,
                validators=[MinValueValidator(1), MaxValueValidator(1000)],
                help_text="Общий лимит сообщений Standard и Smart за 24 часа (1–1000). Ограничение 8/мин остаётся обязательным.",
            ),
        ),
    ]
