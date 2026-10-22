from django.contrib import admin
from .models import NotificationPreference, PushSubscription, PushMessage, PushDelivery


@admin.register(NotificationPreference)
class PreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "instant_enabled", "daily_enabled", "timezone_name", "daily_hour")


@admin.register(PushSubscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "active", "created_at", "updated_at")
    exclude = ("endpoint", "p256dh", "auth")
    readonly_fields = ("user", "active", "created_at", "updated_at")


@admin.register(PushMessage)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "created_at", "read_at")
    readonly_fields = ("user", "kind", "event", "digest_date", "title", "body", "url", "created_at", "read_at")


@admin.register(PushDelivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("message", "state", "attempts", "next_attempt_at", "sent_at")
    readonly_fields = ("message", "subscription", "state", "attempts", "next_attempt_at", "locked_at", "sent_at")
