from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone

class AccessGrant(models.Model):
    # As in BeClients: time-bounded grants are the access source of truth.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saas_access_grants")
    source = models.CharField(max_length=24, choices=[("trial","Trial"),("payment","Payment"),("admin","Admin")])
    source_ref = models.CharField(max_length=128, unique=True)
    plan = models.CharField(max_length=16, choices=[("standard", "Standard"), ("smart", "Smart")], default="standard")
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        indexes = [models.Index(fields=["user","starts_at","ends_at"])]

class SubscriptionPayment(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saas_payments")
    provider = models.CharField(max_length=24, default="tochka")
    plan = models.CharField(max_length=16, choices=[("standard", "Standard"), ("smart", "Smart")], default="standard")
    operation_id = models.CharField(max_length=128, blank=True)
    amount_kopeks = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="RUB")
    status = models.CharField(max_length=16, default="pending", choices=[
        ("pending","Pending"),("paid","Paid"),("failed","Failed"),("refunded","Refunded")])
    period_months = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    paid_at = models.DateTimeField(null=True, blank=True)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)
    payment_url = models.URLField(max_length=2048, blank=True)
    link_expires_at = models.DateTimeField(null=True, blank=True)
    @property
    def amount_rubles(self):
        return Decimal(self.amount_kopeks) / Decimal(100)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["provider","operation_id"], condition=~models.Q(operation_id=""), name="unique_provider_operation")]


class OwnerAISettings(models.Model):
    """Singleton NoSale AI control plane (non-secret configuration only).

    Service credentials belong in the environment/AI Platform, never in this
    table or an editable Django Admin form. Unused until R2/R4 AI integration.
    """
    id = models.PositiveSmallIntegerField(primary_key=True, editable=False)
    ai_enabled = models.BooleanField(default=False, help_text="Выключает AI, но не контроль скидок.")
    gateway_model_id = models.CharField(max_length=120, blank=True, help_text="Точный опубликованный идентификатор модели в AI Platform.")
    prompt_version = models.CharField(max_length=32, default="v1")
    system_instructions = models.TextField(blank=True, help_text="Дополнительные инструкции ассистенту. Запрещено давать право записи в WB.")
    standard_questions_per_day = models.PositiveSmallIntegerField(default=1)
    smart_questions_per_day = models.PositiveSmallIntegerField(default=2)
    smart_briefs_per_day = models.PositiveSmallIntegerField(default=2)
    max_output_tokens = models.PositiveSmallIntegerField(default=500)
    timeout_seconds = models.PositiveSmallIntegerField(default=20)
    monthly_budget_kopeks = models.PositiveIntegerField(default=0, help_text="0 = AI вызовы запрещены даже при включённом переключателе.")
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return "NoSale — AI и лимиты"

    @property
    def ready_for_gateway(self):
        return bool(self.ai_enabled and self.gateway_model_id.strip() and self.monthly_budget_kopeks > 0)

    class Meta:
        verbose_name = "Настройки AI владельца"
        verbose_name_plural = "Настройки AI владельца"


class OwnerTariffOffer(models.Model):
    """Two editable commercial offers, not a second subscription/access ledger."""
    PLAN_CHOICES = [("standard", "Standard"), ("smart", "Smart")]
    slug = models.CharField(max_length=16, primary_key=True, choices=PLAN_CHOICES)
    name = models.CharField(max_length=50)
    price_kopeks = models.PositiveIntegerField(help_text="Цена за один месяц в копейках. Проверяется на сервере.")
    enabled = models.BooleanField(default=False, help_text="При выключении новые покупки этого тарифа запрещены.")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name or self.get_slug_display()

    class Meta:
        verbose_name = "Тариф для продажи"
        verbose_name_plural = "Тарифы для продажи"
        ordering = ["slug"]
