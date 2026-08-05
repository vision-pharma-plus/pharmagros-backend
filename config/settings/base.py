"""
Base settings shared by every environment.

Security posture: defaults here are the *strict* ones. Environment-specific
modules may relax them explicitly (dev.py does), but nothing is insecure
merely because a setting was forgotten.
"""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
    CORS_ALLOWED_ORIGINS=(list, []),
)

# Read .env if present. In production, real environment variables win.
# Accept it beside manage.py or at the repo root; the first hit wins.
for _candidate in (BASE_DIR / ".env", BASE_DIR.parent / ".env"):
    if _candidate.exists():
        env.read_env(str(_candidate))
        break

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    "axes",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_celery_beat",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.catalog",
    "apps.partners",
    "apps.inventory",
    "apps.sales",
    "apps.invoicing",
    "apps.purchasing",
    "apps.accounting",
    "apps.reporting",
    "apps.notifications",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Binds request user/IP/user-agent to a thread-local so model-layer
    # audit signals can record *who* made a change without threading the
    # request object through every service call.
    "apps.core.middleware.AuditContextMiddleware",
    # Applies the user's stored language preference; must follow
    # AuthenticationMiddleware so request.user is resolved.
    "apps.core.middleware.UserLanguageMiddleware",
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        **env.db("DATABASE_URL"),
        # Persistent connections: meaningful latency win under the 100
        # concurrent user target, since Postgres connection setup is not free.
        "CONN_MAX_AGE": env.int("DB_CONN_MAX_AGE", default=60),
        "CONN_HEALTH_CHECKS": True,
        "ATOMIC_REQUESTS": False,  # transactions are scoped in services, not blanket
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = [
    # Axes must come first so lockout is evaluated before credentials.
    "axes.backends.AxesStandaloneBackend",
    "apps.accounts.backends.EmailBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# Argon2 first: memory-hard, the current best practice for password storage.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    # Enforces upper/lower/digit/symbol mix — the "complexity policy" requirement.
    {"NAME": "apps.accounts.validators.ComplexityValidator"},
    # Blocks reuse of the last N passwords.
    {
        "NAME": "apps.accounts.validators.PasswordHistoryValidator",
        "OPTIONS": {"history_length": 5},
    },
]

# --- Account lockout (django-axes) -----------------------------------------
AXES_FAILURE_LIMIT = env.int("AXES_FAILURE_LIMIT", default=5)
AXES_COOLOFF_TIME = timedelta(minutes=env.int("AXES_COOLOFF_MINUTES", default=30))
# Lock on the (username, IP) pair. Locking on username alone lets an attacker
# lock out legitimate users by guessing; locking on IP alone is defeated by
# rotating IPs. The pair is the pragmatic middle ground.
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
AXES_ENABLE_ADMIN = True
AXES_LOCKOUT_CALLABLE = "apps.accounts.lockout.lockout_response"

# ---------------------------------------------------------------------------
# DRF / JWT
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    # Rejects a token whose account has since been suspended, deactivated or
    # had its sessions revoked. Plain JWTAuthentication trusts the token's
    # claims for its full lifetime, which would leave a revoked session
    # working until the access token expired.
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "apps.core.authentication.StatefulJWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        # Deny by default. Every endpoint must opt in to who may reach it.
        "rest_framework.permissions.IsAuthenticated",
        # An account-state gate rather than per-endpoint authorisation: a user
        # owing a password change must be stopped everywhere. Views that set
        # their own `permission_classes` replace this list, so it is also
        # applied defensively inside HasPermission.
        "apps.core.permissions.PasswordChangeNotPending",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.ScopedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "auth": "10/min",       # login / token endpoints
        "password_reset": "5/hour",
        "reports": "60/hour",   # report generation is expensive
        # Each cache miss is a paid upstream call. Generous enough to read a
        # page of notes, tight enough that a loop cannot run up a bill.
        "translate": "120/hour",
        "default": "1000/hour",
    },
}

SIMPLE_JWT = {
    # Short access token limits the blast radius of a leaked token; the
    # refresh flow below rotates and blacklists so a stolen refresh token
    # is single-use and detectable.
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": env("JWT_SIGNING_KEY", default=SECRET_KEY),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "TOKEN_OBTAIN_SERIALIZER": "apps.accounts.serializers.TokenObtainSerializer",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Vision Pharma plus — Management API",
    "DESCRIPTION": (
        "Vision Pharma plus — wholesale pharmaceutical management system for "
        "Burundi. "
        "All monetary values are BIF. Amounts are transmitted as decimal "
        "strings to avoid float precision loss."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    # Several models legitimately have a field named `status` or `language`
    # with different choice sets. Without explicit names, drf-spectacular
    # invents hash-suffixed enums like `Status6d3Enum`, which are meaningless
    # in generated clients. Naming them keeps the frontend's generated types
    # readable and stable across releases.
    "ENUM_NAME_OVERRIDES": {
        "InvoiceStatusEnum": "apps.invoicing.models.InvoiceStatus.choices",
        "SaleStatusEnum": "apps.sales.models.SaleStatus.choices",
        "PurchaseOrderStatusEnum": "apps.purchasing.models.PurchaseOrderStatus.choices",
        "BatchStatusEnum": "apps.inventory.models.BatchStatus.choices",
        "ProductStatusEnum": "apps.catalog.models.ProductStatus.choices",
        "PartnerStatusEnum": "apps.partners.models.PartnerStatus.choices",
        "MovementTypeEnum": "apps.inventory.models.MovementType.choices",
        "PaymentTermsEnum": "apps.partners.models.PaymentTerms.choices",
        "PaymentMethodEnum": "apps.invoicing.models.PaymentMethod.choices",
        "NotificationSeverityEnum": "apps.notifications.models.NotificationSeverity.choices",
        "NotificationCodeEnum": "apps.notifications.models.NotificationCode.choices",
        "AuditActionEnum": "apps.core.models.AuditAction.choices",
        # Reached under two field names — `credit_reason_code` on the invoice
        # and `reason_code` on the credit-note request — so it needs an
        # explicit name to stay a single enum in the generated client.
        "CreditNoteReasonEnum": "apps.invoicing.models.CreditNoteReason.choices",
        "LanguageEnum": "apps.core.translation.LANGUAGE_CHOICES",
        # Supplier-side and expense enums. Each overlaps a customer-side enum
        # on field name (`status`, `method`) but not on values, so without
        # these the generator resolves the clash with hash suffixes.
        "SupplierInvoiceStatusEnum": "apps.purchasing.models.SupplierInvoiceStatus.choices",
        "ExpenseStatusEnum": "apps.accounting.models.ExpenseStatus.choices",
        # SupplierPaymentMethod and ExpensePaymentMethod are the same set of
        # values (PaymentMethod without CREDIT_NOTE, which only settles a
        # customer invoice). They are separate classes so that neither can
        # drift into offering a non-cash settlement, but to the schema they are
        # one choice set and must therefore carry one name — listing both would
        # trip the duplicate check in ENUM_NAME_OVERRIDES.
        "OutgoingPaymentMethodEnum": "apps.purchasing.models.SupplierPaymentMethod.choices",
    },
}

# ---------------------------------------------------------------------------
# Internationalisation — French default, English secondary
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "fr"
LANGUAGES = [("fr", "Français"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Africa/Bujumbura"
USE_I18N = True
USE_TZ = True
# Burundi follows the French/Belgian convention: space thousands separator,
# comma decimal. Handled by Django's fr locale formats; we only force the
# use of localised formatting.
USE_THOUSAND_SEPARATOR = True

# ---------------------------------------------------------------------------
# Money / business rules
# ---------------------------------------------------------------------------
CURRENCY_CODE = "BIF"
# BIF has no circulating subunit, so *documents* round to whole francs.
# Internally we keep 4 dp (see apps.core.money) because unit costs and
# tax bases genuinely need it on wholesale quantities.
CURRENCY_DISPLAY_DECIMALS = 0
MONEY_MAX_DIGITS = 18
MONEY_DECIMAL_PLACES = 4
# Burundi standard VAT (TVA). Overridable per product/customer at runtime.
DEFAULT_VAT_RATE = env.str("DEFAULT_VAT_RATE", default="18.00")

# Ceiling on a manually entered sales discount, in percent.
#
# A discount is the one number on a sale an operator can change that directly
# reduces revenue, and it needs no approval elsewhere in the flow — which makes
# it the easiest lever for both honest error (a mistyped 50 for 5) and
# deliberate abuse. The ceiling is absolute: no permission lifts it, and only
# holders of `sales.apply_discount` may enter a manual discount at all.
# Raising it is a deployment decision, not a counter decision.
#
# A customer's *standing* contractual discount is not subject to this: it was
# agreed when the account was set up, not chosen at the counter. See
# `apps.sales.services._check_discount_limit`.
MAX_MANUAL_DISCOUNT_PERCENT = env.str("MAX_MANUAL_DISCOUNT_PERCENT", default="10.00")

# ---------------------------------------------------------------------------
# Cache / Celery
# ---------------------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("REDIS_URL"),
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=env("REDIS_URL"))
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=env("REDIS_URL"))
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# ---------------------------------------------------------------------------
# Static / media
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

FILE_UPLOAD_MAX_BYTES = env.int("FILE_UPLOAD_MAX_BYTES", default=10 * 1024 * 1024)
FILE_UPLOAD_ALLOWED_EXTENSIONS = ["pdf", "png", "jpg", "jpeg", "xlsx", "csv"]

# ---------------------------------------------------------------------------
# Security headers (production values; dev.py relaxes what it must)
# ---------------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = env.int("SESSION_COOKIE_AGE", default=60 * 60 * 8)
SECURE_REFERRER_POLICY = "same-origin"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True

# The frontend sends X-Language on every request to drive server-side
# translation, and X-Request-ID for log correlation. Neither is a CORS-safe
# header, so both must be allowlisted explicitly — otherwise the browser
# blocks the preflight and reports a generic CORS failure that gives no hint
# which header caused it.
CORS_ALLOW_HEADERS = (
    "accept",
    "authorization",
    "content-type",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
    "x-language",
    "x-request-id",
)

# Lets the frontend read the request ID off a response, so a user-reported
# error can be tied to the exact backend log line.
CORS_EXPOSE_HEADERS = ("x-request-id",)

# Base URL of the Next.js frontend, used to build links in outgoing email
# (password reset, invoice delivery). Must be the public URL, not the
# internal container address.
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:3000")

# ---------------------------------------------------------------------------
# Machine translation
# ---------------------------------------------------------------------------
# Powers the reader-triggered "Translate" control over user-entered notes.
# Claude is preferred for its handling of pharmaceutical vocabulary; without a
# key the service falls back to deep-translator/Google, and without that it
# reports itself unavailable rather than echoing the untranslated source.
#
# Note this sends note text to a third party. Confirm that is acceptable for
# your data before setting a key in production.
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")
TRANSLATION_MODEL = env("TRANSLATION_MODEL", default="claude-sonnet-5")

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@pharmagros.bi")

# ---------------------------------------------------------------------------
# Company identity (printed on invoices / reports)
# ---------------------------------------------------------------------------
COMPANY = {
    "NAME": env("COMPANY_NAME", default="VISION PHARMA PLUS"),
    # Printed under the company name in the invoice masthead. Optional: the
    # block is omitted entirely when this is blank, so an unset tagline
    # leaves no gap rather than an empty line.
    "TAGLINE": env("COMPANY_TAGLINE", default=""),
    "NIF": env("COMPANY_NIF", default="4001902867"),
    "RC": env("COMPANY_RC", default="35325/22"),
    "ADDRESS": env("COMPANY_ADDRESS", default="MUKAZA ; AVENUE DU COMMERCE"),
    "PHONE": env("COMPANY_PHONE", default="+257 68 606 080"),
    "EMAIL": env("COMPANY_EMAIL", default="visionpharmaplus@gmail.com"),
    # Printed in the invoice header: the OBR expects a taxpayer's fiscal
    # centre, sector and legal form to identify the issuer.
    "VAT_REGISTERED": env.bool("COMPANY_VAT_REGISTERED", default=True),
    "TAX_CENTRE": env("COMPANY_TAX_CENTRE", default="DPMC"),
    "SECTOR": env("COMPANY_SECTOR", default="PHARMACIE DE GROS"),
    "LEGAL_FORM": env("COMPANY_LEGAL_FORM", default="SPRL"),
    # Two accounts, one per currency, exactly as they appear on the document.
    "BANK_ACCOUNTS": [
        {
            "bank": env("COMPANY_BANK_1_NAME", default="BCAB A/C NO"),
            "number": env("COMPANY_BANK_1_NUMBER", default="20103972201"),
            "currency": env("COMPANY_BANK_1_CURRENCY", default="BIF"),
        },
        {
            "bank": env("COMPANY_BANK_2_NAME", default="BCAB A/C NO"),
            "number": env("COMPANY_BANK_2_NUMBER", default="20203972201"),
            # Literal, not env(): django-environ reads a leading "$" as a
            # proxy reference to another variable and recurses forever.
            "currency": env("COMPANY_BANK_2_CURRENCY", default="USD"),
        },
    ],
}

# ---------------------------------------------------------------------------
# OBR electronic invoicing (EBMS)
# ---------------------------------------------------------------------------
# Disabled by default. Declaration only becomes possible once the OBR has
# certified the installation and issued a SYSTEM_ID, so switching this on
# before then would queue documents that can never be accepted. Every
# deployment therefore opts in explicitly.
OBR = {
    "ENABLED": env.bool("OBR_ENABLED", default=False),
    # The sandbox and production services are separate deployments with
    # separate credentials. Keeping the base URL in configuration means a
    # change of endpoint — which has happened before — needs no code release.
    "BASE_URL": env("OBR_BASE_URL", default="https://ebms.obr.gov.bi:9443/ebms_api"),
    "USERNAME": env("OBR_USERNAME", default=""),
    "PASSWORD": env("OBR_PASSWORD", default=""),
    # Issued by the OBR on certification; forms the second field of every
    # fiscal signature. Without it, signatures cannot be built.
    "SYSTEM_ID": env("OBR_SYSTEM_ID", default=""),
    "PATHS": {
        "login": env("OBR_PATH_LOGIN", default="login"),
        "add_invoice": env("OBR_PATH_ADD_INVOICE", default="addInvoice"),
        "cancel_invoice": env("OBR_PATH_CANCEL_INVOICE", default="cancelInvoice"),
        "check_tin": env("OBR_PATH_CHECK_TIN", default="checkTIN"),
    },
    # A hung connection must not pin a Celery worker: connect and read
    # timeouts are set separately because a slow response is ordinary on this
    # link while a slow connect means the host is unreachable.
    "CONNECT_TIMEOUT": env.float("OBR_CONNECT_TIMEOUT", default=10.0),
    "READ_TIMEOUT": env.float("OBR_READ_TIMEOUT", default=30.0),
    # After this many failed attempts a document stops being retried and is
    # raised to a human. Retrying a permanently-rejected document forever
    # would bury the real failures in noise.
    "MAX_ATTEMPTS": env.int("OBR_MAX_ATTEMPTS", default=6),
    # Documents dated before go-live are historical and are not declared.
    "START_DATE": env("OBR_START_DATE", default=""),
    "TAXPAYER": {
        "TYPE": env.int("OBR_TAXPAYER_TYPE", default=2),  # 1 natural, 2 legal person
        "VAT_SUBJECT": env.bool("OBR_VAT_SUBJECT", default=True),
        "CONSUMPTION_TAX_SUBJECT": env.bool("OBR_CONSUMPTION_TAX_SUBJECT", default=False),
        "WITHHOLDING_TAX_SUBJECT": env.bool("OBR_WITHHOLDING_TAX_SUBJECT", default=False),
        "TRADE_REGISTER": env("OBR_TRADE_REGISTER", default=""),
        # The OBR records an address as separate administrative divisions
        # rather than one free-text line.
        "PROVINCE": env("OBR_ADDRESS_PROVINCE", default=""),
        "COMMUNE": env("OBR_ADDRESS_COMMUNE", default=""),
        "QUARTIER": env("OBR_ADDRESS_QUARTIER", default=""),
        "AVENUE": env("OBR_ADDRESS_AVENUE", default=""),
        "RUE": env("OBR_ADDRESS_RUE", default=""),
        "NUMBER": env("OBR_ADDRESS_NUMBER", default=""),
    },
}

# ---------------------------------------------------------------------------
# Logging — structured JSON, so production logs are queryable
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "apps.core.logging.JSONFormatter"},
        "plain": {"format": "%(asctime)s %(levelname)-8s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": env("LOG_FORMATTER", default="json"),
        },
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "propagate": True},
        # Dedicated channel: security events must remain greppable even if
        # application log level is raised.
        "security": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "audit": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
