"""
Django settings for backend project.
"""

import sys
from datetime import timedelta
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")
# Local-only overrides (gitignored, never present in production) — lets a developer point at a
# local Postgres instance for pre-commit testing without touching the real .env (which may hold
# production credentials). Only the keys actually present in .env.local are overridden; anything
# not repeated there still comes from .env above.
_local_env_file = BASE_DIR / ".env.local"
if _local_env_file.exists():
    environ.Env.read_env(_local_env_file, overwrite=True)

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")

ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=[
        "clinic-backend-p0km.onrender.com",
        "localhost",
        "127.0.0.1",
    ],
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third-party
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
    "django_celery_beat",
    # business apps
    "common",
    "clinics",
    "accounts",
    "departments",
    "doctors",
    "patients",
    "appointments",
    "consultations",
    "medical_records",
    "prescriptions",
    "billing",
    "payments",
    "communication",
    "reports",
    "subscriptions",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "backend.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "backend.wsgi.application"

# exemple_prod.md §1: production (Render + Supabase) sets a single DATABASE_URL (Supabase's
# pooling connection string); local dev keeps the 5 separate DB_* fields already in .env.example.
if env("DATABASE_URL", default=""):
    DATABASES = {"default": env.db_url("DATABASE_URL")}
elif env("DB_NAME", default=""):
    # .env.local leaves DATABASE_URL blank on purpose to fall back to a local Postgres instance
    # via these 5 fields instead of the production connection string in .env (see .env.local).
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME"),
            "USER": env("DB_USER"),
            "PASSWORD": env("DB_PASSWORD"),
            "HOST": env("DB_HOST"),
            "PORT": env("DB_PORT"),
        }
    }
else:
    # Fails loudly and explicitly here instead of leaving DATABASES unset, which Django would
    # otherwise silently resolve to its "dummy" backend and report as a confusing
    # "supply the ENGINE value" error with no mention of DATABASE_URL (seen in a Render build
    # failure, 2026-09-16).
    raise ImproperlyConfigured(
        "Neither DATABASE_URL nor DB_NAME is set. In Render: Environment -> Environment "
        "Variables -> DATABASE_URL must hold the Supabase connection string (see "
        "exemple_prod.md §1/§2). For local dev, set DATABASE_URL or the DB_* fields in "
        "backend/.env or backend/.env.local."
    )

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    # "default" was missing entirely, which meant any FileField save (e.g. clinic logo upload)
    # raised InvalidStorageError in production, not just in tests (found while adding logo
    # upload tests during the security audit, 2026-09-02).
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- REST Framework -------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "common.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_RATES": {
        # Per-IP ceiling on patient activation request/verify (business/communication-policy.md
        # "OTP generation limited") — the per-principal cooldown in communication/services.py alone
        # doesn't stop someone sweeping many (patient_number, phone) guesses across different targets.
        "patient_activation": "5/hour",
        # Per-IP ceiling on public clinic self-registration — prevents automated bulk creation of
        # trial tenants (each one starts a real Stripe-adjacent trial lifecycle).
        "clinic_registration": "5/hour",
        # Per-IP ceiling on login — without this, password brute-forcing against a known email is
        # unrestricted (security audit finding, 2026-09-02).
        "login": "10/min",
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Clinic Management API",
    "DESCRIPTION": "Multi-tenant SaaS API for medical clinic management.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# --- CORS -------------------------------------------------------------------

# CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=["http://localhost:4200"])

# --- CORS -------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = [
    "http://localhost:4200",
    "http://127.0.0.1:4200",
   "https://clinic-frontend-yrxm.vercel.app",

]

CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
]

CORS_ALLOW_METHODS = [
    "DELETE",
    "GET",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
]
# --- Proxy / HTTPS (exemple_prod.md §2: Render terminates TLS and forwards plain HTTP) ----------

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=[
           "https://clinic-frontend-yrxm.vercel.app",

    ],
)

# --- Celery -------------------------------------------------------------------

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="amqp://guest:guest@localhost:5672//")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/1")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
# Tasks here are fire-and-forget (docs/communication-architecture.md) — nothing ever calls
# .get()/AsyncResult on them. Without this, .delay() opens a synchronous connection to the
# result backend before returning; if that backend (Redis) is unreachable, the connection
# retry loop raises and crashes the caller's request (e.g. appointment creation) even though
# the task itself would have been queued fine on the broker.
CELERY_TASK_IGNORE_RESULT = True
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
# `manage.py test` must never depend on live broker connectivity (CloudAMQP) — tasks run inline,
# synchronously, in the same process instead of being queued. Existing tests
# (e.g. appointments/tests/test_api.py) create appointments, which dispatch notifications, with no
# mocking of Celery, so without this every test run silently required real network access.
if "test" in sys.argv:
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True

# --- Email --------------------------------------------------------------------

# EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@clinic-management.local")

# communication/providers/email_provider.py: empty-string default so manage.py/tests never crash
# when the key is missing — get_email_provider() falls back to DjangoEmailProvider in that case.
RESEND_API_KEY = env("RESEND_API_KEY", default="")
RESEND_FROM_EMAIL = env("RESEND_FROM_EMAIL", default="onboarding@resend.dev")

# --- SMS (httpsms.com) ----------------------------------------------------------

# communication/providers/sms_provider.py: empty-string default so manage.py/tests never crash
# when the key is missing — HttpSmsProvider falls back to logging instead of sending in that case.
HTTPSMS_API_KEY = env("HTTPSMS_API_KEY", default="")
HTTPSMS_FROM_NUMBER = env("HTTPSMS_FROM_NUMBER", default="")

# --- Appointments ---------------------------------------------------------------

# business/workflow-policy.md: "Patient may cancel before the configured deadline" — this is that
# configured value. A patient can self-cancel a pending/confirmed appointment only more than this
# many hours before its scheduled date/time.
APPOINTMENT_CANCELLATION_DEADLINE_HOURS = env.int("APPOINTMENT_CANCELLATION_DEADLINE_HOURS", default=24)

# --- Subscriptions/Stripe --------------------------------------------------------

# Platform subscription billing (business/subscription-billing-policy.md, docs/subscription-billing.md)
# — the clinic paying for its own use of the platform, entirely separate from backend/billing/
# (patient invoicing). Empty-string defaults so manage.py/tests never crash for a missing Stripe
# key — same "architecture ready, credentials not" posture already established for SMS.
SUBSCRIPTION_PAYMENT_PROVIDER = env("SUBSCRIPTION_PAYMENT_PROVIDER", default="stripe")
STRIPE_API_KEY = env("STRIPE_API_KEY", default="")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="")

# PLACEHOLDER Stripe Price IDs — indicative 3-tier x 2-cycle catalog (subscriptions/catalog.py),
# not final pricing. Populate from the Stripe Dashboard once real prices are created.
STRIPE_PRICE_STARTER_MONTHLY = env("STRIPE_PRICE_STARTER_MONTHLY", default="")
STRIPE_PRICE_STARTER_ANNUAL = env("STRIPE_PRICE_STARTER_ANNUAL", default="")
STRIPE_PRICE_PROFESSIONAL_MONTHLY = env("STRIPE_PRICE_PROFESSIONAL_MONTHLY", default="")
STRIPE_PRICE_PROFESSIONAL_ANNUAL = env("STRIPE_PRICE_PROFESSIONAL_ANNUAL", default="")
STRIPE_PRICE_ENTERPRISE_MONTHLY = env("STRIPE_PRICE_ENTERPRISE_MONTHLY", default="")
STRIPE_PRICE_ENTERPRISE_ANNUAL = env("STRIPE_PRICE_ENTERPRISE_ANNUAL", default="")

FRONTEND_BASE_URL = env("FRONTEND_BASE_URL", default="http://localhost:4200")
