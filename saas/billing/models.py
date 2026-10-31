from decimal import Decimal
from django.core.validators import MinValueValidator, MaxValueValidator
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
    web_assistant_enabled = models.BooleanField(
        default=False, help_text="Устаревший ручной чат выключен; AI работает только для плановых сводок."
    )
    web_messages_per_day = models.PositiveSmallIntegerField(
        default=80, validators=[MinValueValidator(1), MaxValueValidator(1000)],
        help_text="Общий лимит сообщений Standard и Smart за 24 часа (1–1000). Ограничение 8/мин остаётся обязательным.",
    )
    gateway_model_id = models.CharField(max_length=120, blank=True, help_text="Точный опубликованный идентификатор модели в AI Platform.")
    prompt_version = models.CharField(max_length=32, default="v1")
    system_instructions = models.TextField(blank=True, help_text="Дополнительные инструкции ассистенту. Запрещено давать право записи в WB.")
    standard_questions_per_day = models.PositiveSmallIntegerField(default=1)
    smart_questions_per_day = models.PositiveSmallIntegerField(default=2)
    smart_briefs_per_day = models.PositiveSmallIntegerField(default=2)
    smart_morning_hour = models.PositiveSmallIntegerField(default=8, validators=[MaxValueValidator(23)], help_text="Утренняя сводка по местному времени пользователя.")
    smart_evening_hour = models.PositiveSmallIntegerField(default=20, validators=[MaxValueValidator(23)], help_text="Вечерняя сводка по местному времени пользователя.")
    smart_reserve_kopeks = models.PositiveIntegerField(default=100, validators=[MinValueValidator(1)], help_text="Резерв на один DeepSeek-ответ в копейках; не фактическая стоимость модели.")
    ai_platform_cost_cap_confirmed = models.BooleanField(default=False, help_text="Реальный отдельный лимит расходов подтверждён в AI Platform.")
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
    price_kopeks = models.PositiveIntegerField(validators=[MinValueValidator(100), MaxValueValidator(10_000_000)], help_text="Цена за месяц в копейках. Редактируется в админке в рублях.")
    enabled = models.BooleanField(default=False, help_text="При выключении новые покупки этого тарифа запрещены.")
    # None retains pre-constructor rights for historical plans. [] explicitly
    # removes all capabilities, including from existing paid/trial grants.
    features = models.JSONField(
        null=True, blank=True, default=None,
        help_text="Возможности действующих подписчиков тарифа. Не управляет оплатой или правами владельца.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name or self.get_slug_display()

    class Meta:
        verbose_name = "Тариф для продажи"
        verbose_name_plural = "Тарифы для продажи"
        ordering = ["slug"]


class OwnerAIGatewayConnection(models.Model):
    """Private per-product AI Platform connection, as in MiniBook gateway settings.

    The key is Fernet-encrypted with the SaaS secret; admin form never displays it.
    Project-specific upstream keys stay exclusively in AI Platform.
    """
    id = models.PositiveSmallIntegerField(primary_key=True, editable=False)
    base_url = models.URLField(default="https://platform.svdev.pro/v1", max_length=255)
    catalog_mode = models.CharField(
        max_length=12, choices=[("curated", "Curated"), ("scoped", "Chat only"), ("full", "Full")],
        default="scoped",
    )
    encrypted_service_token = models.TextField(blank=True, editable=False)
    catalog_models = models.JSONField(default=list, blank=True, editable=False)
    catalog_tool_models = models.JSONField(default=list, blank=True, editable=False)
    last_checked_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_check_ok = models.BooleanField(null=True, blank=True, editable=False)
    last_check_message = models.CharField(max_length=220, blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return "AI Platform — подключение NoSale"

    @property
    def configured(self):
        return bool(self.encrypted_service_token)

    class Meta:
        verbose_name = "Подключение AI Platform"
        verbose_name_plural = "Подключение AI Platform"


class OwnerMCPAdapter(models.Model):
    """Owner-operated registry of reviewed in-process MCP marketplace adapters.

    Only known first-party adapter drivers exist. Enabling a row never installs
    code, adds a new URL, accepts credentials, or enables marketplace writes.
    """
    KNOWN = {
        "wb": ("Wildberries", "read_only_cached"),
        "ozon": ("Ozon", "status_only_unimplemented"),
    }
    slug = models.CharField(
        max_length=16, primary_key=True, editable=False,
        choices=[("wb", "Wildberries"), ("ozon", "Ozon")],
    )
    enabled = models.BooleanField(
        default=True, verbose_name="Разрешён владельцем",
        help_text=(
            "Отключение запрещает вызовы всех MCP-инструментов этой площадки. "
            "Защита WB через прямой API и другие площадки продолжают работать."
        ),
    )
    implementation = models.CharField(
        max_length=32, editable=False, choices=[
            ("read_only_cached", "Встроен: чтение сохранённых данных"),
            ("status_only_unimplemented", "Только статус: API не подключён"),
        ],
    )
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if self.slug not in self.KNOWN:
            raise ValueError("Unsupported MCP marketplace adapter")
        # The implementation type is a compiled safety boundary, not a flag.
        self.implementation = self.KNOWN[self.slug][1]
        super().save(*args, **kwargs)

    @classmethod
    def permits(cls, slug):
        if slug not in cls.KNOWN:
            return False
        return cls.objects.filter(slug=slug, enabled=True).exists()

    def __str__(self):
        return self.KNOWN.get(self.slug, (self.slug, ""))[0]

    class Meta:
        verbose_name = "Адаптер MCP маркетплейса"
        verbose_name_plural = "Адаптеры MCP маркетплейсов"


class OwnerMCPSettings(models.Model):
    """Singleton owner-operated policy for installed read-only MCP tools.

    Endpoint host, credential handling and write bans remain non-configurable
    security invariants, but operational toggles and query sizes live in DB.
    """
    id = models.PositiveSmallIntegerField(primary_key=True, editable=False)
    enabled = models.BooleanField(
        default=True,
        help_text="Общий переключатель внутренних MCP-инструментов. Выключение не останавливает прямую защиту скидок WB.",
    )
    wb_enabled = models.BooleanField(default=True, help_text="Разрешить чтение сохранённых данных WB через MCP.")
    wb_overview_enabled = models.BooleanField(default=True, verbose_name="WB: сводка магазина")
    wb_products_enabled = models.BooleanField(default=True, verbose_name="WB: товары и скидки")
    wb_events_enabled = models.BooleanField(default=True, verbose_name="WB: события")
    wb_promotions_enabled = models.BooleanField(default=True, verbose_name="WB: акции")
    ozon_status_enabled = models.BooleanField(
        default=True, verbose_name="Ozon: только состояние адаптера",
        help_text="Пока API Ozon не подключён, инструмент сообщает только статус not_implemented.",
    )
    max_products = models.PositiveSmallIntegerField(
        default=20, validators=[MinValueValidator(1), MaxValueValidator(20)],
        help_text="Максимум товаров, которые Smart может запросить за один MCP-вызов (1–20).",
    )
    max_events = models.PositiveSmallIntegerField(
        default=20, validators=[MinValueValidator(1), MaxValueValidator(20)],
        help_text="Максимум событий в одном MCP-вызове (1–20).",
    )
    max_promotions = models.PositiveSmallIntegerField(
        default=15, validators=[MinValueValidator(1), MaxValueValidator(15)],
        help_text="Максимум акций в одном MCP-вызове (1–15).",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return "NoSale — MCP и маркетплейсы"

    @classmethod
    def runtime(cls):
        # Fail closed if the singleton was deleted or the migration not applied.
        return cls.objects.filter(pk=1).first()

    def allows(self, name):
        names = {
            "wb_overview": "wb_overview_enabled",
            "wb_products": "wb_products_enabled",
            "wb_events": "wb_events_enabled",
            "wb_promotions": "wb_promotions_enabled",
            "ozon_adapter_status": "ozon_status_enabled",
        }
        if name not in names or not self.enabled:
            return False
        marketplace = "wb" if name.startswith("wb_") else "ozon"
        if name.startswith("wb_") and not self.wb_enabled:
            return False
        # A fresh PostgreSQL read gates every client and server tool call.
        # Missing adapters fail closed; seller-provided data cannot register one.
        return bool(getattr(self, names[name])) and OwnerMCPAdapter.permits(marketplace)

    class Meta:
        verbose_name = "Инструменты MCP владельца"
        verbose_name_plural = "Инструменты MCP владельца"


class OwnerOperationsSettings(models.Model):
    """Live, owner-controlled background-work policy in PostgreSQL.

    Technical network, provider-scope and seller permissions remain separate
    hard safety invariants. Missing singleton fails closed for network work.
    """
    id = models.PositiveSmallIntegerField(primary_key=True, editable=False)
    guard_checks_enabled = models.BooleanField(
        default=True,
        verbose_name="Новые фоновые проверки WB",
        help_text="Опасно отключать: остановятся новые циклы контроля цен/скидок всех продавцов. Уже начатый запрос может завершиться.",
    )
    instant_events_enabled = models.BooleanField(
        default=True, verbose_name="Мгновенные события в Push",
        help_text="Если отключено, события остаются в журнале, но новые push-доставки для них не создаются.",
    )
    daily_digests_enabled = models.BooleanField(
        default=True, verbose_name="Ежедневные сводки Push",
        help_text="Отключает создание новых ежедневных сводок. Не стирает уже созданные.",
    )
    push_delivery_enabled = models.BooleanField(
        default=True, verbose_name="Отправка Web Push",
        help_text="При паузе ожидающие доставки сохраняются для дальнейшей отправки.",
    )
    guard_poll_seconds = models.PositiveSmallIntegerField(
        default=10, validators=[MinValueValidator(5), MaxValueValidator(60)],
        verbose_name="Интервал опроса Guard, секунд (5–60)",
    )
    notifications_poll_seconds = models.PositiveSmallIntegerField(
        default=30, validators=[MinValueValidator(15), MaxValueValidator(300)],
        verbose_name="Интервал опроса Push, секунд (15–300)",
    )
    push_batch_size = models.PositiveSmallIntegerField(
        default=20, validators=[MinValueValidator(1), MaxValueValidator(20)],
        verbose_name="Максимум Push за цикл (1–20)",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def runtime(cls):
        return cls.objects.filter(pk=1).first()

    def __str__(self):
        return "NoSale — фоновые процессы и уведомления"

    class Meta:
        verbose_name = "Фоновые процессы SaaS"
        verbose_name_plural = "Фоновые процессы SaaS"
