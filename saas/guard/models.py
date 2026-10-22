from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class GuardAccount(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="guard_account")
    token_ciphertext = models.TextField(blank=True)
    token_hint = models.CharField(max_length=32, blank=True)
    enabled = models.BooleanField(default=False)
    check_requested = models.BooleanField(default=False)
    apply_all_requested = models.BooleanField(default=False)
    apply_requested_ids = models.JSONField(default=list)
    default_allowed_discount = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(99)])
    interval_minutes = models.PositiveIntegerField(default=10, validators=[MinValueValidator(5), MaxValueValidator(1440)])
    max_fixes = models.PositiveIntegerField(default=20, validators=[MinValueValidator(1), MaxValueValidator(1000)])
    next_check_at = models.DateTimeField(default=timezone.now)
    lock_until = models.DateTimeField(null=True, blank=True)
    last_check_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    rules_version = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.user.email or self.user.username


class Product(models.Model):
    account = models.ForeignKey(GuardAccount, on_delete=models.CASCADE, related_name="products")
    nm_id = models.PositiveBigIntegerField()
    vendor_code = models.CharField(max_length=255, blank=True)
    title = models.CharField(max_length=500, blank=True)
    photo_url = models.URLField(blank=True)
    price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    discounted_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="RUB")
    current_discount = models.PositiveSmallIntegerField(default=0)
    allowed_discount_override = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(99)])
    checked_at = models.DateTimeField(default=timezone.now)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "nm_id"], name="one_product_per_account")]
        ordering = ["vendor_code", "nm_id"]

    @property
    def allowed_discount(self):
        if self.allowed_discount_override is not None:
            return self.allowed_discount_override
        return self.account.default_allowed_discount


class Upload(models.Model):
    account = models.ForeignKey(GuardAccount, on_delete=models.CASCADE, related_name="uploads")
    upload_id = models.PositiveBigIntegerField()
    status = models.CharField(max_length=16, default="pending")
    product_ids = models.JSONField(default=list)
    corrected_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    checked_at = models.DateTimeField(null=True, blank=True)
    details = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "upload_id"], name="one_upload_per_account")]


class GuardEvent(models.Model):
    account = models.ForeignKey(GuardAccount, on_delete=models.CASCADE, related_name="events")
    created_at = models.DateTimeField(default=timezone.now)
    kind = models.CharField(max_length=32)
    nm_id = models.PositiveBigIntegerField(null=True, blank=True)
    message = models.TextField()

    class Meta:
        ordering = ["-created_at", "-id"]
