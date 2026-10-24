from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class NotificationPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_preferences")
    instant_enabled = models.BooleanField(default=True)
    daily_enabled = models.BooleanField(default=True)
    timezone_name = models.CharField(max_length=64, default="Europe/Moscow")
    daily_hour = models.PositiveSmallIntegerField(default=20)


class PushSubscription(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.URLField(max_length=2048, unique=True)
    p256dh = models.CharField(max_length=256)
    auth = models.CharField(max_length=256)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)


class PushMessage(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_messages")
    kind = models.CharField(max_length=12, choices=[("event", "Event"), ("digest", "Digest")])
    event = models.ForeignKey("guard.GuardEvent", on_delete=models.CASCADE, null=True, blank=True, related_name="push_messages")
    digest_date = models.DateField(null=True, blank=True)
    title = models.CharField(max_length=180)
    body = models.CharField(max_length=400)
    url = models.CharField(max_length=150, default="/notifications/")
    created_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["event"], condition=Q(event__isnull=False), name="push_event_once"),
            models.UniqueConstraint(fields=["user", "digest_date"], condition=Q(digest_date__isnull=False), name="push_digest_once_per_day"),
        ]
        ordering = ["-created_at", "-id"]


class PushDelivery(models.Model):
    message = models.ForeignKey(PushMessage, on_delete=models.CASCADE, related_name="deliveries")
    subscription = models.ForeignKey(PushSubscription, on_delete=models.CASCADE, related_name="deliveries")
    state = models.CharField(max_length=12, default="pending", choices=[
        ("pending", "Pending"), ("sending", "Sending"), ("sent", "Sent"),
        ("gone", "Gone"), ("failed", "Failed"),
    ])
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    locked_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["message", "subscription"], name="push_one_delivery_per_device")]
        indexes = [models.Index(fields=["state", "next_attempt_at"], name="push_deliv_state_bba31a_idx")]
