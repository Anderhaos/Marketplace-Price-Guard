from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("guard", "0007_guardevent_details_read_at_upload_change_details"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailPinChallenge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254)),
                ("purpose", models.CharField(choices=[("register", "Регистрация"), ("recover", "Восстановление PIN")], max_length=16)),
                ("code_hash", models.CharField(blank=True, max_length=256)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("sent_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField()),
                ("verified_until", models.DateTimeField(blank=True, null=True)),
            ],
        ),
        migrations.CreateModel(
            name="EmailPinLoginThrottle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254, unique=True)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("first_failed_at", models.DateTimeField(blank=True, null=True)),
                ("locked_until", models.DateTimeField(blank=True, null=True)),
            ],
        ),
        migrations.AddConstraint(
            model_name="emailpinchallenge",
            constraint=models.UniqueConstraint(fields=("email", "purpose"), name="saas_email_challenge_one_active"),
        ),
    ]
