from django.contrib import admin
from .models import GuardAccount, GuardEvent, Product, Upload


@admin.register(GuardAccount)
class GuardAccountAdmin(admin.ModelAdmin):
    list_display = ("user", "enabled", "token_hint", "interval_minutes", "last_check_at")
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
