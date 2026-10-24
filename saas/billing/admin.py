from decimal import Decimal
from django import forms
from django.contrib import admin, messages
from .models import AccessGrant, SubscriptionPayment, OwnerAISettings, OwnerTariffOffer, OwnerAIGatewayConnection, OwnerMCPSettings
from .ai_platform_connection import (
    PlatformConnectionError, normalize_base_url, save_gateway_token, verify_gateway,
)

@admin.register(AccessGrant)
class AccessGrantAdmin(admin.ModelAdmin):
    list_display = ("user", "source", "plan", "starts_at", "ends_at", "revoked_at")
    search_fields = ("user__email","source_ref")

@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ("user","provider","plan","amount_kopeks","status","created_at")
    readonly_fields = ("user","provider","plan","operation_id","amount_kopeks","currency","status","period_months","created_at","paid_at")


class OwnerAISettingsForm(forms.ModelForm):
    class Meta:
        model = OwnerAISettings
        exclude = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        gateway = OwnerAIGatewayConnection.objects.filter(
            pk=1, last_check_ok=True,
        ).first()
        if gateway and gateway.catalog_models:
            choices = [("", "Выберите модель AI Platform")]
            choices.extend((m, m) for m in gateway.catalog_models)
            value = self.instance.gateway_model_id
            # Keep historical selection visible, but require new selections
            # to be available in the verified catalog.
            if value and value not in gateway.catalog_models:
                choices.append((value, f"{value} (больше не доступна)"))
            self.fields["gateway_model_id"] = forms.ChoiceField(
                choices=choices, required=False, label="Модель AI Platform",
                help_text="Только проверенные модели этого подключения.",
            )


@admin.register(OwnerAISettings)
class OwnerAISettingsAdmin(admin.ModelAdmin):
    form = OwnerAISettingsForm
    """Operational controls require superuser, not mere staff permission."""
    list_display = ("__str__", "web_assistant_enabled", "web_messages_per_day", "ai_enabled", "gateway_model_id", "standard_questions_per_day",
                    "smart_questions_per_day", "monthly_budget_kopeks", "updated_at")
    readonly_fields = ("updated_at",)
    fieldsets = (
        ("Веб-помощник Standard / Smart", {
            "fields": ("web_assistant_enabled", "web_messages_per_day"),
            "description": (
                "Встроенный чат без Telegram: отключение блокирует оба режима, "
                "но не контроль скидок WB. Общий суточный лимит не отменяет защиту 8/мин."
            ),
        }),
        ("AI Platform", {"fields": ("ai_enabled", "gateway_model_id", "prompt_version",
                                     "system_instructions", "timeout_seconds", "max_output_tokens")}),
        ("Лимиты и бюджет", {
            "fields": ("standard_questions_per_day", "smart_questions_per_day",
                       "smart_briefs_per_day", "monthly_budget_kopeks"),
            "description": (
                "Бюджет в копейках ограничивает локальные резервы AI-запросов, "
                "но не является точным счётом провайдера. Фактические расходы "
                "и независимый месячный лимит должны быть настроены в AI Platform."
            ),
        }),
        ("Состояние", {"fields": ("updated_at",)}),
    )

    def has_module_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_add_permission(self, request):
        return self.has_view_permission(request) and not OwnerAISettings.objects.exists()

    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


class OwnerTariffOfferForm(forms.ModelForm):
    price_rub = forms.DecimalField(
        label="Цена за месяц, ₽", max_digits=8, decimal_places=2,
        min_value=Decimal("1.00"), max_value=Decimal("100000.00"),
    )

    class Meta:
        model = OwnerTariffOffer
        fields = ("slug", "name", "enabled")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["price_rub"].initial = Decimal(self.instance.price_kopeks) / 100

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.price_kopeks = int(self.cleaned_data["price_rub"] * 100)
        if commit:
            obj.save()
        return obj


@admin.register(OwnerTariffOffer)
class OwnerTariffOfferAdmin(admin.ModelAdmin):
    form = OwnerTariffOfferForm
    fields = ("slug", "name", "price_rub", "enabled", "updated_at")
    list_display = ("slug", "name", "price_kopeks", "enabled", "updated_at")
    list_filter = ("enabled",)
    readonly_fields = ("updated_at",)

    def has_module_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request) and OwnerTariffOffer.objects.count() < 2

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields.append("slug")
        return fields



class OwnerAIGatewayConnectionForm(forms.ModelForm):
    service_token = forms.CharField(
        label="Ключ проекта AI Platform",
        help_text="Новый ключ сохранится зашифрованно. Оставьте пустым, чтобы сохранить действующий.",
        required=False, widget=forms.PasswordInput(render_value=False),
        max_length=2048,
    )

    class Meta:
        model = OwnerAIGatewayConnection
        fields = ("base_url", "catalog_mode")

    def clean_base_url(self):
        try:
            return normalize_base_url(self.cleaned_data["base_url"])
        except (ValueError, PlatformConnectionError) as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean_service_token(self):
        token = self.cleaned_data["service_token"].strip()
        if token and (len(token) < 16 or any(char.isspace() for char in token)):
            raise forms.ValidationError("Укажите корректный ключ AI Platform без пробелов")
        return token

    def save(self, commit=True):
        connection = super().save(commit=False)
        old = OwnerAIGatewayConnection.objects.filter(pk=1).values(
            "base_url", "catalog_mode"
        ).first()
        if old and (
            old["base_url"] != connection.base_url
            or old["catalog_mode"] != connection.catalog_mode
        ):
            # Changing either catalog scope or endpoint invalidates a previous
            # authorization snapshot, even when the secret itself is unchanged.
            connection.catalog_models = []
            connection.last_check_ok = None
            connection.last_checked_at = None
            connection.last_check_message = ""
        if self.cleaned_data["service_token"]:
            save_gateway_token(connection, self.cleaned_data["service_token"])
        if commit:
            connection.save()
        return connection


@admin.register(OwnerAIGatewayConnection)
class OwnerAIGatewayConnectionAdmin(admin.ModelAdmin):
    form = OwnerAIGatewayConnectionForm
    list_display = ("__str__", "base_url", "configured", "last_check_ok", "last_checked_at", "last_check_message")
    fields = ("base_url", "catalog_mode", "service_token", "key_status",
              "last_checked_at", "last_check_ok", "last_check_message", "catalog_preview")
    readonly_fields = ("key_status", "last_checked_at", "last_check_ok", "last_check_message", "catalog_preview")
    actions = ("verify_selected",)

    @admin.display(description="Сервисный ключ")
    def key_status(self, obj):
        return "Сохранён (зашифрован)" if obj and obj.configured else "Не задан"

    @admin.display(description="Доступные модели")
    def catalog_preview(self, obj):
        return ", ".join(obj.catalog_models[:40]) if obj and obj.catalog_models else "Не проверено. Выберите запись в списке и действие «Проверить подключение»."

    @admin.action(description="Проверить подключение и загрузить модели")
    def verify_selected(self, request, queryset):
        if not self.has_change_permission(request):
            self.message_user(request, "Нет прав для проверки подключения", level=messages.ERROR)
            return
        for connection in queryset:
            try:
                found = verify_gateway(connection)
            except (ValueError, PlatformConnectionError):
                self.message_user(request, "Проверка AI Platform не прошла. Смотрите статус подключения.", level=messages.ERROR)
            else:
                self.message_user(request, f"AI Platform подключена; моделей: {len(found)}", level=messages.SUCCESS)

    def has_module_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request) and not OwnerAIGatewayConnection.objects.exists()

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OwnerMCPSettings)
class OwnerMCPSettingsAdmin(admin.ModelAdmin):
    """Only the actual NoSale superuser controls MCP and marketplace modules."""
    list_display = (
        "__str__", "enabled", "wb_enabled", "wb_products_enabled",
        "wb_events_enabled", "wb_promotions_enabled", "updated_at",
    )
    readonly_fields = ("updated_at",)
    fieldsets = (
        ("Внутренний MCP", {
            "fields": ("enabled",),
            "description": (
                "Единый сервер NoSale MCP. Только чтение, внутренний Docker-доступ. "
                "Он не заменяет прямой WB API и не меняет цены продавцов."
            ),
        }),
        ("Wildberries — разрешённые инструменты", {
            "fields": (
                "wb_enabled", "wb_overview_enabled", "wb_products_enabled",
                "wb_events_enabled", "wb_promotions_enabled",
            ),
        }),
        ("Лимиты запросов Smart / MCP", {
            "fields": ("max_products", "max_events", "max_promotions"),
            "description": "Действуют немедленно на сервере, включая прямые вызовы MCP.",
        }),
        ("Ozon", {
            "fields": ("ozon_status_enabled",),
            "description": "Пока доступен только честный статус not_implemented. Реального Ozon API ещё нет.",
        }),
        ("История конфигурации", {"fields": ("updated_at",)}),
    )

    def has_module_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request) and not OwnerMCPSettings.objects.exists()

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False
