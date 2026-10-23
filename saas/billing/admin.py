from django.contrib import admin
from .models import AccessGrant, SubscriptionPayment, OwnerAISettings

@admin.register(AccessGrant)
class AccessGrantAdmin(admin.ModelAdmin):
    list_display = ("user", "source", "plan", "starts_at", "ends_at", "revoked_at")
    search_fields = ("user__email","source_ref")

@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ("user","provider","plan","amount_kopeks","status","created_at")
    readonly_fields = ("user","provider","plan","operation_id","amount_kopeks","currency","status","period_months","created_at","paid_at")


@admin.register(OwnerAISettings)
class OwnerAISettingsAdmin(admin.ModelAdmin):
    """Operational controls require superuser, not mere staff permission."""
    list_display = ("__str__", "ai_enabled", "gateway_model_id", "standard_questions_per_day",
                    "smart_questions_per_day", "monthly_budget_kopeks", "updated_at")
    readonly_fields = ("updated_at",)
    fieldsets = (
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
