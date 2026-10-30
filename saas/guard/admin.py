from django.contrib import admin
from .models import AssistantAIRequest, GuardAccount, GuardEvent, Product, Upload


@admin.register(GuardAccount)
class GuardAccountAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "marketplace", "enabled", "token_hint", "interval_minutes", "last_check_at")
    list_filter = ("enabled",)
    readonly_fields = ("token_ciphertext", "token_hint", "last_check_at", "last_error", "lock_until", "next_check_at")
    search_fields = ("user__email", "user__username")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("account", "nm_id", "vendor_code", "current_discount", "checked_at")
    search_fields = ("vendor_code", "nm_id", "account__user__email")


@admin.register(Upload)
class UploadAdmin(admin.ModelAdmin):
    list_display = ("account", "upload_id", "status", "created_at", "checked_at")
    readonly_fields = ("account", "upload_id", "status", "product_ids", "created_at", "checked_at", "details")


@admin.register(GuardEvent)
class GuardEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "account", "kind", "nm_id", "message")
    readonly_fields = ("account", "created_at", "kind", "nm_id", "message")


@admin.register(AssistantAIRequest)
class SmartAIUsageAdmin(admin.ModelAdmin):
    """Read-only audit of provider calls: never store prompts or token secrets."""
    list_display = ("created_at", "user", "account", "source", "status",
                    "model_id", "reserved_rub", "input_tokens", "output_tokens")
    list_filter = ("source", "status", "model_id")
    search_fields = ("user__email", "account__name")
    ordering = ("-created_at",)
    readonly_fields = ("user", "account", "created_at", "source", "status",
                       "model_id", "reserved_rub", "input_tokens", "output_tokens")
    list_per_page = 40

    @admin.display(description="Зарезервировано, ₽")
    def reserved_rub(self, obj):
        return f"{obj.reserved_kopeks / 100:.2f}"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_module_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)
