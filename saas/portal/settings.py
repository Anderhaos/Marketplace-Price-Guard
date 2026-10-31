import os
from pathlib import Path
from email.utils import parseaddr

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
    "portal.security_headers.NoSaleSecurityHeadersMiddleware",
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
# New deployments must explicitly opt into discount writes. The network
# switch and the read-only switch are independent fail-closed controls.
SAAS_WB_READ_ONLY = env_bool("SAAS_WB_READ_ONLY", True)

# Dedicated opt-in Finance read channel; independent from price/discount WB
# authorization. Never enabled just because the general WB network is enabled.
NOSALE_WB_FINANCE_ENABLED = env_bool("NOSALE_WB_FINANCE_ENABLED", False)
SAAS_ACCESS_ENFORCED = env_bool("SAAS_ACCESS_ENFORCED", True)
# Same acquiring contract as MiniBook, separate NoSale merchant configuration.
# Fail closed until a merchant, verified RS256 webhook key, and approved RUB price exist.
TOCHKA_BILLING_ENABLED = env_bool("NOSALE_TOCHKA_ENABLED", False)
TOCHKA_API_BASE = os.environ.get("NOSALE_TOCHKA_API_BASE", "https://enter.tochka.com/uapi")
TOCHKA_JWT_TOKEN = os.environ.get("NOSALE_TOCHKA_JWT_TOKEN", "")
TOCHKA_CUSTOMER_CODE = os.environ.get("NOSALE_TOCHKA_CUSTOMER_CODE", "")
TOCHKA_MERCHANT_ID = os.environ.get("NOSALE_TOCHKA_MERCHANT_ID", "")
TOCHKA_WEBHOOK_JWK_JSON = os.environ.get("NOSALE_TOCHKA_WEBHOOK_JWK_JSON", "")
# Receipt values are owner/tax-adviser decisions. Never guess tax regime or VAT.
NOSALE_TOCHKA_TAX_SYSTEM_CODE = os.environ.get("NOSALE_TOCHKA_TAX_SYSTEM_CODE", "").strip()
NOSALE_TOCHKA_VAT_TYPE = os.environ.get("NOSALE_TOCHKA_VAT_TYPE", "").strip()
NOSALE_TOCHKA_RECEIPT_PAYMENT_METHOD = os.environ.get("NOSALE_TOCHKA_RECEIPT_PAYMENT_METHOD", "").strip()
NOSALE_MONTHLY_PRICE_RUB = os.environ.get("NOSALE_MONTHLY_PRICE_RUB", "0")
# Standard keeps the legacy price setting for existing deployments; new prices stay OFF until approved.
NOSALE_STANDARD_MONTHLY_PRICE_RUB = os.environ.get("NOSALE_STANDARD_MONTHLY_PRICE_RUB")
NOSALE_SMART_MONTHLY_PRICE_RUB = os.environ.get("NOSALE_SMART_MONTHLY_PRICE_RUB", "0")
NOSALE_FISCALIZATION_CONFIRMED = env_bool("NOSALE_FISCALIZATION_CONFIRMED", False)
NOSALE_PUBLIC_BASE_URL = os.environ.get("NOSALE_PUBLIC_BASE_URL", "https://nosale.svdev.pro").strip().rstrip("/")
NOSALE_PAYMENT_RETURN_BASE = os.environ.get("NOSALE_PAYMENT_RETURN_BASE", NOSALE_PUBLIC_BASE_URL).strip().rstrip("/")
# Central AI Platform Gateway; independent of bank, WB and seller API tokens.
# Disabled until NoSale project enrollment issues its own service token.
NOSALE_AI_GATEWAY_URL = os.environ.get("NOSALE_AI_GATEWAY_URL", "")
NOSALE_AI_SERVICE_TOKEN = os.environ.get("NOSALE_AI_SERVICE_TOKEN", "")
# Conservative local per-call budget reservation, not an actual provider invoice.
# Provider billing/actual USD budget are enforced by AI Platform.
NOSALE_AI_RESERVE_KOPEKS_PER_CALL = int(os.environ.get("NOSALE_AI_RESERVE_KOPEKS_PER_CALL", "100"))
if TOCHKA_BILLING_ENABLED:
    if not NOSALE_FISCALIZATION_CONFIRMED:
        raise RuntimeError("NoSale acquiring requires configured 54-FZ fiscal receipt issuance")
    # OwnerTariffOffer rows in PostgreSQL are authoritative. Prices are checked
    # against the *enabled* tariff at checkout, not against obsolete env defaults.
    if NOSALE_TOCHKA_TAX_SYSTEM_CODE not in {"osn", "usn_income", "usn_income_outcome", "esn", "patent"}:
        raise RuntimeError("NoSale requires an explicitly confirmed receipt tax system")
    if NOSALE_TOCHKA_VAT_TYPE not in {"none", "vat0", "vat5", "vat7", "vat10", "vat22", "vat105", "vat107", "vat110", "vat122"}:
        raise RuntimeError("NoSale requires an explicitly confirmed receipt VAT rate")
    if NOSALE_TOCHKA_RECEIPT_PAYMENT_METHOD not in {"full_payment", "full_prepayment"}:
        raise RuntimeError("NoSale requires an explicitly confirmed receipt payment method")
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
# Fail closed if a developer enables live WB reads without the HTTP write guard.
if DEBUG and SAAS_PIN_AUTH_ENABLED and SAAS_WB_NETWORK_ENABLED and not SAAS_WB_READ_ONLY:
    raise RuntimeError("DEBUG SaaS WB network access requires SAAS_WB_READ_ONLY=1")
EMAIL_HOST = os.environ.get("SMTP_HOST", "")
EMAIL_PORT = int(os.environ.get("SMTP_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("SMTP_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_PASSWORD_FILE = os.environ.get("SMTP_PASSWORD_FILE", "").strip()
if EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend" and SMTP_PASSWORD_FILE:
    # An optional root-owned bind mount keeps SMTP credentials out of .env.
    import stat
    secret_path = Path(SMTP_PASSWORD_FILE)
    try:
        mode = stat.S_IMODE(secret_path.stat().st_mode)
        if mode & 0o077:
            raise RuntimeError("SMTP password file must not be group/world readable")
        EMAIL_HOST_PASSWORD = secret_path.read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        raise RuntimeError("SMTP password file cannot be read") from exc
    if not EMAIL_HOST_PASSWORD:
        raise RuntimeError("SMTP password file must not be empty")
EMAIL_USE_TLS = env_bool("SMTP_USE_TLS", True)
EMAIL_USE_SSL = env_bool("SMTP_USE_SSL", False)
EMAIL_TIMEOUT = int(os.environ.get("SMTP_TIMEOUT", "12"))
DEFAULT_FROM_EMAIL = os.environ.get("SMTP_FROM", "noreply@mpnosale.svdev.pro")
if not DEBUG and SAAS_PIN_AUTH_ENABLED and not REQUIRE_EMAIL_VERIFICATION:
    raise RuntimeError("PIN authentication requires email verification")
# Authenticated SMTP is permitted only over an encrypted connection, and the
# visible From identity must be the same mailbox that logged in to SMTP.
sender_address = parseaddr(DEFAULT_FROM_EMAIL)[1].lower()
smtp_tls_valid = (
    (EMAIL_PORT == 465 and EMAIL_USE_SSL and not EMAIL_USE_TLS)
    or (EMAIL_PORT == 587 and EMAIL_USE_TLS and not EMAIL_USE_SSL)
)
smtp_ready = (
    EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend"
    and bool(EMAIL_HOST) and bool(EMAIL_HOST_PASSWORD)
    and bool(EMAIL_HOST_USER) and "@" in sender_address
    and EMAIL_HOST_USER.strip().lower() == sender_address
    and smtp_tls_valid and 0 < EMAIL_TIMEOUT <= 30
)
relay_ready = (
    EMAIL_BACKEND == "portal.email_backend.RelayEmailBackend"
    and MAIL_RELAY_URL == "https://mail.beclients.ru/svdev-relay.php"
    and len(MAIL_RELAY_TOKEN) >= 32
)
if not DEBUG and SAAS_PIN_AUTH_ENABLED and not (smtp_ready or relay_ready):
    raise RuntimeError("PIN authentication requires trusted relay or authenticated TLS/SSL SMTP")
if not DEBUG and REQUIRE_EMAIL_VERIFICATION and not (smtp_ready or relay_ready):
    raise RuntimeError("Production registration requires verified mail transport")
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
