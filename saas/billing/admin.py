from decimal import Decimal
from django import forms
from django.contrib import admin, messages
from django.contrib.admin.models import LogEntry
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from .models import AccessGrant, SubscriptionPayment, PaymentRefundAttempt, OwnerAISettings, OwnerTariffOffer, OwnerAIGatewayConnection, OwnerMCPSettings, OwnerOperationsSettings, OwnerMCPAdapter
from .refunds import RefundUnavailable, start_full_refund
from .tochka import configured
from .ai_platform_connection import (
    PlatformConnectionError, normalize_base_url, save_gateway_token, verify_gateway,
)

@admin.register(AccessGrant)
class AccessGrantAdmin(admin.ModelAdmin):
    list_display = ("user", "source", "plan", "starts_at", "ends_at", "revoked_at")
    search_fields = ("user__email","source_ref")

class RefundConfirmForm(forms.Form):
    confirm_refund = forms.BooleanField(
        label="Подтверждаю полный возврат исходной суммы без частичного удержания",
        required=True,
    )


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    """Read-only bank ledger; refunds are an explicit confirmed owner action."""
    list_display = ("id", "user", "plan", "amount_kopeks", "status",
                    "refund_state", "refund_action", "created_at")
    list_filter = ("status", "plan")
    search_fields = ("user__email", "operation_id")
    readonly_fields = tuple(field.name for field in SubscriptionPayment._meta.fields)
    actions = None

    def _owner(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_module_permission(self, request):
        return self._owner(request)

    def has_view_permission(self, request, obj=None):
        return self._owner(request)

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Возврат")
    def refund_state(self, obj):
        attempt = PaymentRefundAttempt.objects.filter(payment_id=obj.pk).first()
        return attempt.get_status_display() if attempt else "Нет запроса"

    @admin.display(description="Управление")
    def refund_action(self, obj):
        if obj.status != "paid" or obj.provider != "tochka":
            return "—"
        if PaymentRefundAttempt.objects.filter(payment_id=obj.pk).exists():
            return "Запрос уже отправлен"
        return format_html(
            '<a href="{}">Вернуть {} ₽</a>',
            reverse("admin:billing_subscriptionpayment_refund", args=[obj.pk]),
            f"{Decimal(obj.amount_kopeks) / 100:.2f}",
        )

    def get_urls(self):
        own = [
            path(
                "<int:payment_id>/refund/",
                self.admin_site.admin_view(self.refund_payment_view),
                name="billing_subscriptionpayment_refund",
            ),
        ]
        return own + super().get_urls()

    def refund_payment_view(self, request, payment_id):
        if not self._owner(request):
            return HttpResponseForbidden("Только владелец NoSale может оформлять возвраты.")
        if request.method not in ("GET", "POST"):
            return HttpResponseNotAllowed(["GET", "POST"])
        payment = get_object_or_404(
            SubscriptionPayment.objects.select_related("user"), pk=payment_id,
        )
        attempt = PaymentRefundAttempt.objects.filter(payment=payment).first()
        if request.method == "POST":
            form = RefundConfirmForm(request.POST)
            if (payment.status != "paid" or payment.provider != "tochka"
                    or attempt is not None):
                self.message_user(
                    request, "Платёж уже возвращён или запрос возврата уже создан.",
                    level=messages.WARNING,
                )
                return redirect("admin:billing_subscriptionpayment_changelist")
            if form.is_valid():
                try:
                    result = start_full_refund(payment.pk, request.user)
                except RefundUnavailable as exc:
                    self.message_user(request, str(exc), level=messages.ERROR)
                else:
                    if result.status == "confirmed":
                        self.message_user(
                            request, "Точка подтвердила возврат. Платный доступ отозван.",
                            level=messages.SUCCESS,
                        )
                    elif result.status == "submitted":
                        self.message_user(
                            request, "Запрос принят Точкой. Доступ будет отозван после подтверждения банком.",
                            level=messages.SUCCESS,
                        )
                    else:
                        self.message_user(
                            request, "Статус возврата неоднозначен. Проверьте журнал и Точку; повторная отправка заблокирована.",
                            level=messages.WARNING,
                        )
                return redirect("admin:billing_subscriptionpayment_changelist")
        else:
            form = RefundConfirmForm()
        context = {
            **self.admin_site.each_context(request),
            "title": "Подтверждение полного возврата",
            "opts": self.model._meta,
            "payment": payment,
            "amount_rub": f"{Decimal(payment.amount_kopeks) / 100:.2f}",
            "form": form,
            "refund_eligible": (payment.provider == "tochka"
                                and payment.status == "paid"
                                and attempt is None and configured()),
            "existing_attempt": attempt,
            "back_url": reverse("admin:billing_subscriptionpayment_changelist"),
        }
        return TemplateResponse(
            request, "admin/billing/subscriptionpayment/refund_confirmation.html", context,
        )


@admin.register(PaymentRefundAttempt)
class PaymentRefundAttemptAdmin(admin.ModelAdmin):
    """Immutable owner-facing audit of every refund POST / uncertain outcome."""
    list_display = ("id", "payment", "requested_by", "amount_kopeks",
                    "status", "bank_status", "failure_code", "created_at", "updated_at")
    list_filter = ("status",)
    search_fields = ("payment__operation_id", "payment__user__email")
    readonly_fields = tuple(field.name for field in PaymentRefundAttempt._meta.fields)
    actions = None

    def has_module_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class OwnerAISettingsForm(forms.ModelForm):
    monthly_budget_rub = forms.DecimalField(
        label="Бюджет Smart за месяц, ₽", min_value=Decimal("0.00"),
        max_value=Decimal("100000.00"), max_digits=8, decimal_places=2,
        help_text="Общий месячный лимит для всех пользователей NoSale. Это резерв, не счёт провайдера."
    )
    smart_reserve_rub = forms.DecimalField(
        label="Резерв на одну сводку, ₽", min_value=Decimal("0.01"),
        max_value=Decimal("1000.00"), max_digits=7, decimal_places=2,
        help_text="Сколько бюджета резервировать на одно краткое резюме DeepSeek."
    )
    class Meta:
        model = OwnerAISettings
        # These legacy fields are not consumed by any runtime path. They
        # remain in the DB for compatibility but must not imply working limits.
        exclude = ("standard_questions_per_day", "smart_questions_per_day", "web_assistant_enabled", "web_messages_per_day", "prompt_version", "monthly_budget_kopeks", "smart_reserve_kopeks")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["monthly_budget_rub"].initial = Decimal(self.instance.monthly_budget_kopeks) / 100
        self.fields["smart_reserve_rub"].initial = Decimal(self.instance.smart_reserve_kopeks) / 100
        self.fields["smart_briefs_per_day"].help_text = (
            "0 — не отправлять; 1 — только вечером; 2 — утром и вечером по местному времени продавца."
        )
        gateway = OwnerAIGatewayConnection.objects.filter(
            pk=1, last_check_ok=True,
        ).first()
        if gateway and gateway.catalog_models:
            choices = [("", "Выберите модель DeepSeek")]
            choices.extend((model, model) for model in gateway.catalog_models if model.startswith("deepseek/"))
            value = self.instance.gateway_model_id
            # Keep historical selection visible, but require new selections
            # to be available in the verified catalog.
            if value and value not in gateway.catalog_models:
                choices.append((value, f"{value} (больше не доступна)"))
            self.fields["gateway_model_id"] = forms.ChoiceField(
                choices=choices, required=False, label="Модель AI Platform",
                help_text="Модель только редактирует готовую сводку NoSale: один короткий запрос, без выбора инструментов.",
            )


    def clean(self):
        values = super().clean()
        morning, evening = values.get("smart_morning_hour"), values.get("smart_evening_hour")
        if morning is not None and evening is not None and morning >= evening:
            raise forms.ValidationError("Утренняя сводка должна быть раньше вечерней.")
        if values.get("ai_enabled") and not values.get("ai_platform_cost_cap_confirmed"):
            raise forms.ValidationError("Подтвердите реальный месячный лимит расходов в AI Platform.")
        if values.get("gateway_model_id") and not values["gateway_model_id"].startswith("deepseek/"):
            raise forms.ValidationError("Для плановой аналитики выберите модель DeepSeek.")
        return values

    def save(self, commit=True):
        item = super().save(commit=False)
        item.monthly_budget_kopeks = int(self.cleaned_data["monthly_budget_rub"] * 100)
        item.smart_reserve_kopeks = int(self.cleaned_data["smart_reserve_rub"] * 100)
        if commit:
            item.save()
        return item

    def clean_smart_briefs_per_day(self):
        count = self.cleaned_data["smart_briefs_per_day"]
        if count not in (0, 1, 2):
            raise forms.ValidationError("Допустимо 0, 1 или 2 сводки в день.")
        return count


@admin.register(OwnerAISettings)
class OwnerAISettingsAdmin(admin.ModelAdmin):
    form = OwnerAISettingsForm
    """Operational controls require superuser, not mere staff permission."""
    list_display = ("__str__", "ai_enabled", "gateway_model_id", "smart_briefs_per_day", "monthly_budget_rub_display", "updated_at")
    readonly_fields = ("updated_at",)

    @admin.display(description="Бюджет Smart, ₽")
    def monthly_budget_rub_display(self, obj):
        return f"{Decimal(obj.monthly_budget_kopeks) / 100:.2f}"

    fieldsets = (
        ("DeepSeek — только плановые рекомендации", {
            "fields": ("ai_enabled", "ai_platform_cost_cap_confirmed", "gateway_model_id",
                       "system_instructions", "timeout_seconds", "max_output_tokens"),
            "description": "Только обработка проверенных скриптовых фактов. Ручной чат и вызовы WB исключены.",
        }),
        ("Расписание пользователей", {
            "fields": ("smart_briefs_per_day", "smart_morning_hour", "smart_evening_hour"),
            "description": "Время по часовому поясу каждого продавца. 0, 1 или 2 сводки в день.",
        }),
        ("Бюджет в рублях", {
            "fields": ("monthly_budget_rub", "smart_reserve_rub"),
            "description": "Лимиты NoSale отражают резервы, не подтверждённый расход. Отдельный лимит в AI Platform обязателен.",
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


from .access import FEATURE_CHOICES, PLAN_CAPABILITIES


class OwnerTariffOfferForm(forms.ModelForm):
    features = forms.MultipleChoiceField(
        choices=FEATURE_CHOICES, widget=forms.CheckboxSelectMultiple,
        required=False, label="Доступные функции тарифа",
        help_text="Применяется немедленно к действующим подпискам; не включает продажи.",
    )
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
        self.fields["features"].initial = (
            self.instance.features if self.instance.features is not None
            else sorted(PLAN_CAPABILITIES.get(self.instance.slug, ()))
        )

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.price_kopeks = int(self.cleaned_data["price_rub"] * 100)
        obj.features = self.cleaned_data["features"]
        if commit:
            obj.save()
        return obj


@admin.register(OwnerTariffOffer)
class OwnerTariffOfferAdmin(admin.ModelAdmin):
    form = OwnerTariffOfferForm
    fields = ("slug", "name", "price_rub", "enabled", "features", "updated_at")
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
            connection.catalog_tool_models = []
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


@admin.register(OwnerMCPAdapter)
class OwnerMCPAdapterAdmin(admin.ModelAdmin):
    """Owner-managed activation of reviewed, non-installable adapter drivers."""
    list_display = ("__str__", "slug", "enabled", "adapter_state", "updated_at")
    list_filter = ("enabled",)
    fields = ("slug", "enabled", "adapter_state", "updated_at")
    readonly_fields = ("slug", "adapter_state", "updated_at")
    actions = None

    @admin.display(description="Реальная функциональность")
    def adapter_state(self, obj):
        if obj is None:
            return "Не зарегистрирован"
        if obj.slug == "wb":
            return "Встроенный: только сохранённые товары, скидки и акции WB"
        if obj.slug == "ozon":
            return "Ozon API НЕ ПОДКЛЮЧЁН. Только статус not_implemented"
        return "Неизвестный адаптер запрещён"

    def has_module_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

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


@admin.register(LogEntry)
class OwnerConfigurationAuditAdmin(admin.ModelAdmin):
    """Read-only Django admin change log; never stores changed secret values."""
    list_display = ("action_time", "user", "content_type", "object_repr", "action_name")
    list_filter = ("action_flag", "content_type")
    search_fields = ("user__username", "object_repr")
    ordering = ("-action_time",)
    list_per_page = 35
    readonly_fields = (
        "action_time", "user", "content_type", "object_id",
        "object_repr", "action_flag", "get_change_message",
    )
    fields = readonly_fields
    actions = None

    @admin.display(description="Действие")
    def action_name(self, obj):
        return "Создано" if obj.is_addition() else "Изменено" if obj.is_change() else "Удалено"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Show operational settings changes, not arbitrary uploaded seller data.
        return qs.filter(
            content_type__app_label="billing",
            content_type__model__in=(
                "ownermcpsettings", "owneraisettings",
                "owneraigatewayconnection", "ownertariffoffer", "owneroperationssettings", "ownermcpadapter",
            ),
        ).select_related("user", "content_type")

    def has_module_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OwnerOperationsSettings)
class OwnerOperationsSettingsAdmin(admin.ModelAdmin):
    """Persisted global background-work controls. Seller portal never sees this."""
    list_display = (
        "__str__", "guard_checks_enabled", "instant_events_enabled",
        "daily_digests_enabled", "push_delivery_enabled", "updated_at",
    )
    readonly_fields = ("updated_at",)
    fieldsets = (
        ("Мониторинг Wildberries", {
            "fields": ("guard_checks_enabled", "guard_poll_seconds"),
            "description": (
                "Отключение фоновых проверок WB приостанавливает защиту скидок для ВСЕХ магазинов! "
                "Оно не отменяет уже начатый цикл и не меняет разрешения WB API. "
                "Прямой мониторинг также ограничен обязательными квотами Wildberries."
            ),
        }),
        ("Уведомления для продавцов", {
            "fields": (
                "instant_events_enabled", "daily_digests_enabled",
                "push_delivery_enabled", "notifications_poll_seconds", "push_batch_size",
            ),
            "description": (
                "Пауза доставки не удаляет очередь. Продавцы сохраняют личные настройки подписок. "
                "Изменения применяются при следующем цикле, без редактирования файлов или перезапуска."
            ),
        }),
        ("Изменено", {"fields": ("updated_at",)}),
    )

    def has_module_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request) and not OwnerOperationsSettings.objects.exists()

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False
