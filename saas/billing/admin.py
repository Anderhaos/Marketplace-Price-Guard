from django.contrib import admin
from .models import AccessGrant, SubscriptionPayment

@admin.register(AccessGrant)
class AccessGrantAdmin(admin.ModelAdmin):
    list_display = ("user", "source", "starts_at", "ends_at", "revoked_at")
    search_fields = ("user__email","source_ref")

@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ("user","provider","amount_kopeks","status","created_at")
    readonly_fields = ("user","provider","operation_id","amount_kopeks","currency","status","period_months","created_at","paid_at")
