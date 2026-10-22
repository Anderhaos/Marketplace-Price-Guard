from django.conf import settings
from django.db import migrations, models
from django.db.models import Q
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("guard", "0007_guardevent_details_read_at_upload_change_details"),
    ]

    operations = [
        migrations.CreateModel(
            name="NotificationPreference",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("instant_enabled", models.BooleanField(default=True)),
                ("daily_enabled", models.BooleanField(default=True)),
                ("timezone_name", models.CharField(default="Europe/Moscow", max_length=64)),
                ("daily_hour", models.PositiveSmallIntegerField(default=20)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="push_preferences", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="PushSubscription",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("endpoint", models.URLField(max_length=2048, unique=True)),
                ("p256dh", models.CharField(max_length=256)),
                ("auth", models.CharField(max_length=256)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="push_subscriptions", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="PushMessage",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("event", "Event"), ("digest", "Digest")], max_length=12)),
                ("digest_date", models.DateField(blank=True, null=True)),
                ("title", models.CharField(max_length=180)),
                ("body", models.CharField(max_length=400)),
                ("url", models.CharField(default="/notifications/", max_length=150)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                ("event", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="push_messages", to="guard.guardevent")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="push_messages", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="PushDelivery",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("state", models.CharField(choices=[("pending", "Pending"), ("sending", "Sending"), ("sent", "Sent"), ("gone", "Gone"), ("failed", "Failed")], default="pending", max_length=12)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("locked_at", models.DateTimeField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("message", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="deliveries", to="push.pushmessage")),
                ("subscription", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="deliveries", to="push.pushsubscription")),
            ],
        ),
        migrations.AddConstraint(
            model_name="pushmessage",
            constraint=models.UniqueConstraint(condition=Q(event__isnull=False), fields=("event",), name="push_event_once"),
        ),
        migrations.AddConstraint(
            model_name="pushmessage",
            constraint=models.UniqueConstraint(condition=Q(digest_date__isnull=False), fields=("user", "digest_date"), name="push_digest_once_per_day"),
        ),
        migrations.AddConstraint(
            model_name="pushdelivery",
            constraint=models.UniqueConstraint(fields=("message", "subscription"), name="push_one_delivery_per_device"),
        ),
        migrations.AddIndex(
            model_name="pushdelivery",
            index=models.Index(fields=["state", "next_attempt_at"], name="push_deliv_state_bba31a_idx"),
        ),
    ]
