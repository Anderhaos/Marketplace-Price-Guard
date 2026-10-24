from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class GuardAccount(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="seller_stores")
    name = models.CharField(max_length=90, default="Магазин WB")
    marketplace = models.CharField(max_length=16, default="wb", choices=[("wb", "Wildberries"), ("ozon", "Ozon")])
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
    initial_sync_completed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    rules_version = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["user", "marketplace"])]

    def __str__(self):
        return f"{self.name} ({self.get_marketplace_display()})"


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
    change_details = models.JSONField(default=list, blank=True)
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
    details = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class EmailPinChallenge(models.Model):
    """Single active, rate-limited email challenge per recipient and purpose."""
    PURPOSE_REGISTER = "register"
    PURPOSE_RECOVER = "recover"
    PURPOSE_CHOICES = [(PURPOSE_REGISTER, "Регистрация"), (PURPOSE_RECOVER, "Восстановление PIN")]

    email = models.EmailField(max_length=254)
    purpose = models.CharField(max_length=16, choices=PURPOSE_CHOICES)
    code_hash = models.CharField(max_length=256, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    sent_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    verified_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["email", "purpose"], name="saas_email_challenge_one_active")]


class EmailPinLoginThrottle(models.Model):
    """Persisted across Gunicorn workers; limits guesses against six-digit PINs."""
    email = models.EmailField(max_length=254, unique=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    lock_count = models.PositiveSmallIntegerField(default=0)
    first_failed_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)


class AssistantMessage(models.Model):
    """Bounded, tenant-scoped web assistant history; no secrets or raw WB payloads."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="nosale_assistant_messages")
    account = models.ForeignKey(GuardAccount, on_delete=models.CASCADE, related_name="assistant_messages")
    question = models.CharField(max_length=400)
    answer = models.TextField(max_length=2000)
    mode = models.CharField(max_length=8, choices=[("basic", "Базовый"), ("smart", "AI Smart")], default="basic")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "account", "created_at"], name="guard_assis_user_id_593b14_idx")]

    def __str__(self):
        return f"Помощник: магазин {self.account_id}, {self.created_at:%Y-%m-%d}"


class AssistantAIRequest(models.Model):
    """Atomic server-side reservation for paid AI questions. Never stores prompts."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="nosale_ai_requests")
    account = models.ForeignKey(GuardAccount, on_delete=models.CASCADE, related_name="ai_requests")
    created_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=12, default="reserved", choices=[
        ("reserved", "Reserved"), ("success", "Success"), ("failed", "Failed"),
    ])
    model_id = models.CharField(max_length=120)
    reserved_kopeks = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["user", "created_at"], name="guard_ai_req_user_time_idx")]

    def __str__(self):
        return f"Smart AI request {self.pk} ({self.status})"


class PromotionSnapshot(models.Model):
    """Last read-only campaign audit for one seller-owned WB store.

    JSON stores only curated campaign/product facts, never WB API credentials.
    """
    account = models.OneToOneField(
        GuardAccount, on_delete=models.CASCADE, related_name="promotion_snapshot",
    )
    promotions = models.JSONField(default=list)
    partial = models.BooleanField(default=True)
    fetched_at = models.DateTimeField(null=True, blank=True)
    next_refresh_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=220, blank=True)

    def __str__(self):
        return f"Акции WB: магазин {self.account_id}"
