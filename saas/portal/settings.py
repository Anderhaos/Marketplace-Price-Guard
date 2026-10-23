import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.environ.get(name, "1" if default else "0").lower() in {"1", "true", "yes"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
DEBUG = env_bool("DJANGO_DEBUG")
if not SECRET_KEY and not DEBUG:
    raise RuntimeError("DJANGO_SECRET_KEY is required")
if not SECRET_KEY:
    SECRET_KEY = "development-only-change-before-deploy"
if not DEBUG and (len(SECRET_KEY) < 32 or SECRET_KEY == "generate-a-long-random-value" or SECRET_KEY.startswith("REPLACE_")):
    raise RuntimeError("DJANGO_SECRET_KEY must be a generated random value")

ALLOWED_HOSTS = [host.strip() for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if host.strip()]
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if origin.strip()]
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "guard", "billing", "push.apps.PushConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "guard.onboarding_gate.RequireWbConnectionMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "portal.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "portal" / "templates"], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.debug", "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages",
        "guard.context_processors.notification_count",
    ]},
}]
WSGI_APPLICATION = "portal.wsgi.application"
# SaaS is isolated from the legacy database; sqlite permitted only for tests.
if DEBUG and env_bool("SAAS_TEST_SQLITE"):
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "saas-test.sqlite3",
        # SQLite is only for isolated localhost dev. IMMEDIATE acquires the
        # write reservation at BEGIN and avoids mid-transaction lock upgrades
        # when PIN creation overlaps the browser's other requests.
        "OPTIONS": {"timeout": 30, "transaction_mode": "IMMEDIATE"},
    }}
else:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("SAAS_DB_NAME", "mpnosale_saas"),
        "USER": os.environ.get("SAAS_DB_USER", "mpnosale_saas"),
        "PASSWORD": os.environ.get("SAAS_DB_PASSWORD", ""),
        "HOST": os.environ.get("SAAS_DB_HOST", "db"),
        "PORT": os.environ.get("SAAS_DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }}
    if not DEBUG and (not DATABASES["default"]["PASSWORD"] or DATABASES["default"]["PASSWORD"].startswith("REPLACE_")):
        raise RuntimeError("Generated SAAS_DB_PASSWORD required for production")
SAAS_WB_NETWORK_ENABLED = env_bool("SAAS_WB_NETWORK_ENABLED", False)
SAAS_WB_TOKEN_VALIDATION_ENABLED = env_bool("SAAS_WB_TOKEN_VALIDATION_ENABLED", False)
SAAS_WB_READ_ONLY = env_bool("SAAS_WB_READ_ONLY", False)
SAAS_ACCESS_ENFORCED = env_bool("SAAS_ACCESS_ENFORCED", True)
# Same acquiring contract as MiniBook, separate NoSale merchant configuration.
# Fail closed until a merchant, verified RS256 webhook key, and approved RUB price exist.
TOCHKA_BILLING_ENABLED = env_bool("NOSALE_TOCHKA_ENABLED", False)
TOCHKA_API_BASE = os.environ.get("NOSALE_TOCHKA_API_BASE", "https://enter.tochka.com/uapi")
TOCHKA_JWT_TOKEN = os.environ.get("NOSALE_TOCHKA_JWT_TOKEN", "")
TOCHKA_CUSTOMER_CODE = os.environ.get("NOSALE_TOCHKA_CUSTOMER_CODE", "")
TOCHKA_MERCHANT_ID = os.environ.get("NOSALE_TOCHKA_MERCHANT_ID", "")
TOCHKA_WEBHOOK_JWK_JSON = os.environ.get("NOSALE_TOCHKA_WEBHOOK_JWK_JSON", "")
NOSALE_MONTHLY_PRICE_RUB = os.environ.get("NOSALE_MONTHLY_PRICE_RUB", "0")
NOSALE_FISCALIZATION_CONFIRMED = env_bool("NOSALE_FISCALIZATION_CONFIRMED", False)
NOSALE_PAYMENT_RETURN_BASE = "https://nosale.svdev.pro"
if TOCHKA_BILLING_ENABLED:
    if not NOSALE_FISCALIZATION_CONFIRMED:
        raise RuntimeError("NoSale acquiring requires configured 54-FZ fiscal receipt issuance")
    from decimal import Decimal, InvalidOperation
    try:
        _amount = Decimal(NOSALE_MONTHLY_PRICE_RUB)
    except InvalidOperation as exc:
        raise RuntimeError("NoSale price must be a fixed positive RUB amount") from exc
    if _amount < 1 or _amount > 100000 or _amount.as_tuple().exponent < -2:
        raise RuntimeError("NoSale price must be an approved positive RUB amount")
    if not all((TOCHKA_JWT_TOKEN, TOCHKA_CUSTOMER_CODE, TOCHKA_MERCHANT_ID, TOCHKA_WEBHOOK_JWK_JSON)):
        raise RuntimeError("NoSale Tochka merchant/JWT/webhook public key must be separately provisioned")
    if TOCHKA_API_BASE != "https://enter.tochka.com/uapi":
        raise RuntimeError("Production acquiring endpoint must use Tochka public API")

PUSH_VAPID_PUBLIC_KEY = os.environ.get("PUSH_VAPID_PUBLIC_KEY", "")
PUSH_VAPID_PRIVATE_KEY = os.environ.get("PUSH_VAPID_PRIVATE_KEY", "")
PUSH_VAPID_SUBJECT = os.environ.get("PUSH_VAPID_SUBJECT", "mailto:notifications@svdev.pro")
if not DEBUG and not SAAS_ACCESS_ENFORCED:
    raise RuntimeError("Production may not disable SaaS access enforcement")
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
# Dashboard assets live in portal/static (portal is not an installed Django app).
STATICFILES_DIRS = [BASE_DIR / "portal" / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {"staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
# Diagnostic boundary: record HTTPS relay acceptance latency only.
# Do not log email addresses, message bodies, authentication tokens, or OTPs.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"mail_relay_console": {"class": "logging.StreamHandler"}},
    "loggers": {"portal.email_backend": {
        "handlers": ["mail_relay_console"],
        "level": "INFO",
        "propagate": False,
    }},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
EMAIL_BACKEND = os.environ.get("DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
MAIL_RELAY_URL = os.environ.get("MAIL_RELAY_URL", "")
MAIL_RELAY_TOKEN = os.environ.get("MAIL_RELAY_TOKEN", "")
REQUIRE_EMAIL_VERIFICATION = env_bool("REQUIRE_EMAIL_VERIFICATION", True)
SAAS_PIN_AUTH_ENABLED = env_bool("SAAS_PIN_AUTH_ENABLED", False)
EMAIL_HOST = os.environ.get("SMTP_HOST", "")
EMAIL_PORT = int(os.environ.get("SMTP_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("SMTP_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_USE_TLS = env_bool("SMTP_USE_TLS", True)
DEFAULT_FROM_EMAIL = os.environ.get("SMTP_FROM", "noreply@mpnosale.svdev.pro")
if not DEBUG and SAAS_PIN_AUTH_ENABLED and not REQUIRE_EMAIL_VERIFICATION:
    raise RuntimeError("PIN authentication requires email verification")
if not DEBUG and SAAS_PIN_AUTH_ENABLED and EMAIL_BACKEND != "portal.email_backend.RelayEmailBackend":
    raise RuntimeError("PIN authentication requires the trusted HTTPS relay")
if not DEBUG and REQUIRE_EMAIL_VERIFICATION:
    smtp_ready = (EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend"
                  and EMAIL_HOST and EMAIL_HOST_USER and EMAIL_HOST_PASSWORD)
    relay_ready = (EMAIL_BACKEND == "portal.email_backend.RelayEmailBackend"
                   and MAIL_RELAY_URL == "https://mail.beclients.ru/svdev-relay.php"
                   and len(MAIL_RELAY_TOKEN) >= 32)
    if not (smtp_ready or relay_ready):
        raise RuntimeError("Production registration requires a configured mail transport")
if not DEBUG and not os.environ.get("TOKEN_ENCRYPTION_KEY"):
    raise RuntimeError("Production requires TOKEN_ENCRYPTION_KEY")
if not DEBUG:
    from cryptography.fernet import Fernet
    try:
        Fernet(os.environ["TOKEN_ENCRYPTION_KEY"].encode("ascii"))
    except (ValueError, TypeError) as error:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must be a generated Fernet key") from error
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if env_bool("TRUST_PROXY") else None
SESSION_COOKIE_SECURE = env_bool("SECURE_COOKIES", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("SECURE_COOKIES", not DEBUG)
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", not DEBUG)
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
