import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="GuardAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token_ciphertext", models.TextField(blank=True)),
                ("token_hint", models.CharField(blank=True, max_length=32)),
                ("enabled", models.BooleanField(default=False)),
                ("default_allowed_discount", models.PositiveSmallIntegerField(default=0, validators=[django.core.validators.MaxValueValidator(99)])),
                ("interval_minutes", models.PositiveIntegerField(default=10, validators=[django.core.validators.MinValueValidator(5), django.core.validators.MaxValueValidator(1440)])),
                ("max_fixes", models.PositiveIntegerField(default=20, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(1000)])),
                ("next_check_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("lock_until", models.DateTimeField(blank=True, null=True)),
                ("last_check_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                ("rules_version", models.PositiveIntegerField(default=0)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="guard_account", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="Product",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nm_id", models.PositiveBigIntegerField()),
                ("vendor_code", models.CharField(blank=True, max_length=255)),
                ("price", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("discounted_price", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("currency", models.CharField(default="RUB", max_length=3)),
                ("current_discount", models.PositiveSmallIntegerField(default=0)),
                ("allowed_discount_override", models.PositiveSmallIntegerField(blank=True, null=True, validators=[django.core.validators.MaxValueValidator(99)])),
                ("checked_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("active", models.BooleanField(default=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="products", to="guard.guardaccount")),
            ],
            options={"ordering": ["vendor_code", "nm_id"]},
        ),
        migrations.CreateModel(
            name="Upload",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("upload_id", models.PositiveBigIntegerField()),
                ("status", models.CharField(default="pending", max_length=16)),
                ("product_ids", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("checked_at", models.DateTimeField(blank=True, null=True)),
                ("details", models.TextField(blank=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="uploads", to="guard.guardaccount")),
            ],
        ),
        migrations.CreateModel(
            name="GuardEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("kind", models.CharField(max_length=32)),
                ("nm_id", models.PositiveBigIntegerField(blank=True, null=True)),
                ("message", models.TextField()),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="guard.guardaccount")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddConstraint(model_name="product", constraint=models.UniqueConstraint(fields=("account", "nm_id"), name="one_product_per_account")),
        migrations.AddConstraint(model_name="upload", constraint=models.UniqueConstraint(fields=("account", "upload_id"), name="one_upload_per_account")),
    ]
