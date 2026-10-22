from django.conf import settings
from django.db import models
from django.utils import timezone

class AccessGrant(models.Model):
    # As in BeClients: time-bounded grants are the access source of truth.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saas_access_grants")
    source = models.CharField(max_length=24, choices=[("trial","Trial"),("payment","Payment"),("admin","Admin")])
    source_ref = models.CharField(max_length=128, unique=True)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        indexes = [models.Index(fields=["user","starts_at","ends_at"])]

class SubscriptionPayment(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saas_payments")
    provider = models.CharField(max_length=24, default="tochka")
    operation_id = models.CharField(max_length=128, blank=True)
    amount_kopeks = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="RUB")
    status = models.CharField(max_length=16, default="pending", choices=[
        ("pending","Pending"),("paid","Paid"),("failed","Failed"),("refunded","Refunded")])
    period_months = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    paid_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["provider","operation_id"], condition=~models.Q(operation_id=""), name="unique_provider_operation")]
