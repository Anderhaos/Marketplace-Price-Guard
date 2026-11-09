from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("billing", "0014_tariff_features"),
    ]
    operations = [
        migrations.CreateModel(
            name="PaymentRefundAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("bank_operation_id", models.CharField(max_length=128)),
                ("amount_kopeks", models.PositiveIntegerField()),
                ("status", models.CharField(choices=[
                    ("sending", "Запрос зафиксирован"),
                    ("submitted", "Банк принял запрос"),
                    ("confirmed", "Возврат подтверждён"),
                    ("uncertain", "Требуется проверка банка"),
                    ("rejected", "Банк отклонил запрос"),
                ], default="sending", max_length=16)),
                ("bank_status", models.CharField(blank=True, max_length=32)),
                ("bank_refund_order_id", models.CharField(blank=True, max_length=128)),
                ("failure_code", models.CharField(blank=True, max_length=40)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("payment", models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT, related_name="refund_attempt", to="billing.subscriptionpayment",
                )),
                ("requested_by", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT, related_name="requested_nosale_refunds", to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"verbose_name": "Запрос возврата Точки", "verbose_name_plural": "Журнал запросов возврата Точки", "ordering": ["-created_at"]},
        ),
    ]
