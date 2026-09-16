"""
Réglages Django du projet backend.
"""

import sys
from datetime import timedelta
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")
# Surcharges locales uniquement (ignorées par git, jamais présentes en production) — permet à un
# développeur de pointer vers une instance Postgres locale pour des tests avant commit, sans
# toucher au vrai .env (qui peut contenir des identifiants de production). Seules les clés
# réellement présentes dans .env.local sont surchargées ; tout ce qui n'y est pas répété vient
# toujours de .env ci-dessus.
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
    # bibliothèques tierces
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
    "django_celery_beat",
    # applications métier
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

# exemple_prod.md §1 : en production (Render + Supabase), une seule DATABASE_URL est définie
# (la chaîne de connexion pooling de Supabase) ; en dev local, on garde les 5 champs DB_* séparés
# déjà présents dans .env.example.
if env("DATABASE_URL", default=""):
    DATABASES = {"default": env.db_url("DATABASE_URL")}
elif env("DB_NAME", default=""):
    # .env.local laisse volontairement DATABASE_URL vide pour retomber sur une instance Postgres
    # locale via ces 5 champs, au lieu de la chaîne de connexion de production dans .env (voir
    # .env.local).
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
    # Échoue ici de façon explicite et bruyante, plutôt que de laisser DATABASES non défini, ce
    # que Django résoudrait silencieusement vers son backend "dummy" et signalerait par une
    # erreur confuse "supply the ENGINE value" sans mentionner DATABASE_URL (rencontré lors d'un
    # échec de build Render, 2026-09-16).
    raise ImproperlyConfigured(
        "Ni DATABASE_URL ni DB_NAME ne sont définis. Sur Render : Environment -> Environment "
        "Variables -> DATABASE_URL doit contenir la chaîne de connexion Supabase (voir "
        "exemple_prod.md §1/§2). En dev local, définir DATABASE_URL ou les champs DB_* dans "
        "backend/.env ou backend/.env.local."
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
    # "default" était totalement absent, ce qui faisait qu'un enregistrement FileField (ex.
    # upload du logo de la clinique) levait InvalidStorageError en production, pas seulement
    # dans les tests (découvert en ajoutant les tests d'upload du logo pendant l'audit de
    # sécurité, 2026-09-02).
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
        # Plafond par IP sur la demande/vérification d'activation patient (business/
        # communication-policy.md "OTP generation limited") — le délai de refroidissement par
        # principal dans communication/services.py ne suffit pas seul à empêcher quelqu'un de
        # tester en masse des combinaisons (patient_number, phone) sur des cibles différentes.
        "patient_activation": "5/hour",
        # Plafond par IP sur l'auto-inscription publique d'une clinique — empêche la création
        # automatisée en masse de tenants d'essai (chacun démarre un vrai cycle de vie d'essai
        # lié à Stripe).
        "clinic_registration": "5/hour",
        # Plafond par IP sur la connexion — sans cela, le brute-forcing de mot de passe contre un
        # email connu n'est pas limité (constat de l'audit de sécurité, 2026-09-02).
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
# --- Proxy / HTTPS (exemple_prod.md §2 : Render termine le TLS et transmet en HTTP simple) ------

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
# Les tâches ici sont "fire-and-forget" (docs/communication-architecture.md) — rien n'appelle
# jamais .get()/AsyncResult dessus. Sans ce réglage, .delay() ouvre une connexion synchrone vers
# le backend de résultats avant de retourner ; si ce backend (Redis) est injoignable, la boucle
# de nouvelle tentative de connexion lève une exception et fait planter la requête appelante
# (ex. création d'un rendez-vous) alors même que la tâche aurait été correctement mise en file
# sur le broker.
CELERY_TASK_IGNORE_RESULT = True
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
# `manage.py test` ne doit jamais dépendre d'une connectivité réelle au broker (CloudAMQP) — les
# tâches s'exécutent en ligne, de façon synchrone, dans le même processus plutôt que d'être mises
# en file. Les tests existants (ex. appointments/tests/test_api.py) créent des rendez-vous, ce qui
# déclenche des notifications, sans aucun mock de Celery ; sans ce réglage, chaque exécution de
# test nécessiterait silencieusement un accès réseau réel.
if "test" in sys.argv:
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True

# --- Email --------------------------------------------------------------------

# EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@clinic-management.local")

# communication/providers/email_provider.py : valeur par défaut vide pour que manage.py/les
# tests ne plantent jamais quand la clé est absente — get_email_provider() retombe alors sur
# DjangoEmailProvider.
RESEND_API_KEY = env("RESEND_API_KEY", default="")
RESEND_FROM_EMAIL = env("RESEND_FROM_EMAIL", default="onboarding@resend.dev")

# --- SMS (httpsms.com) ----------------------------------------------------------

# communication/providers/sms_provider.py : valeur par défaut vide pour que manage.py/les tests
# ne plantent jamais quand la clé est absente — HttpSmsProvider se contente alors de logger au
# lieu d'envoyer.
HTTPSMS_API_KEY = env("HTTPSMS_API_KEY", default="")
HTTPSMS_FROM_NUMBER = env("HTTPSMS_FROM_NUMBER", default="")

# --- Appointments ---------------------------------------------------------------

# business/workflow-policy.md : "Patient may cancel before the configured deadline" — voici cette
# valeur configurée. Un patient ne peut annuler lui-même un rendez-vous en attente/confirmé que
# plus de ce nombre d'heures avant sa date/heure prévue.
APPOINTMENT_CANCELLATION_DEADLINE_HOURS = env.int("APPOINTMENT_CANCELLATION_DEADLINE_HOURS", default=24)

# --- Subscriptions/Stripe --------------------------------------------------------

# Facturation de l'abonnement plateforme (business/subscription-billing-policy.md,
# docs/subscription-billing.md) — la clinique qui paie pour son propre usage de la plateforme,
# entièrement distinct de backend/billing/ (facturation des patients). Valeurs par défaut vides
# pour que manage.py/les tests ne plantent jamais faute de clé Stripe — même posture "architecture
# prête, identifiants pas encore là" déjà établie pour le SMS.
SUBSCRIPTION_PAYMENT_PROVIDER = env("SUBSCRIPTION_PAYMENT_PROVIDER", default="stripe")
STRIPE_API_KEY = env("STRIPE_API_KEY", default="")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="")

# Price ID Stripe TEMPORAIRES — catalogue indicatif 3 paliers x 2 cycles (subscriptions/catalog.py),
# pas une tarification définitive. À renseigner depuis le Dashboard Stripe une fois les vrais
# prix créés.
STRIPE_PRICE_STARTER_MONTHLY = env("STRIPE_PRICE_STARTER_MONTHLY", default="")
STRIPE_PRICE_STARTER_ANNUAL = env("STRIPE_PRICE_STARTER_ANNUAL", default="")
STRIPE_PRICE_PROFESSIONAL_MONTHLY = env("STRIPE_PRICE_PROFESSIONAL_MONTHLY", default="")
STRIPE_PRICE_PROFESSIONAL_ANNUAL = env("STRIPE_PRICE_PROFESSIONAL_ANNUAL", default="")
STRIPE_PRICE_ENTERPRISE_MONTHLY = env("STRIPE_PRICE_ENTERPRISE_MONTHLY", default="")
STRIPE_PRICE_ENTERPRISE_ANNUAL = env("STRIPE_PRICE_ENTERPRISE_ANNUAL", default="")

FRONTEND_BASE_URL = env("FRONTEND_BASE_URL", default="http://localhost:4200")
