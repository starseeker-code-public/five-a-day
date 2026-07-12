import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

# One env file, one source of truth. To switch environments, rename one of
# .env.development / .env.testing / .env.production to .env before bringing
# the stack up. Docker-injected process env vars (compose `environment:` or
# Cloud Run `--set-env-vars`) already live in os.environ and take precedence
# over the file by dotenv semantics.
_ENV_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_ENV_ROOT / ".env", override=False)

BASE_DIR = Path(__file__).resolve().parent.parent

# ============================================================================
# APP VERSION
# ============================================================================
# NOTA: Usa `make version x.y.z` para actualizar ambos sitios a la vez:
#   - pyproject.toml (campo version)
#   - README.md (badge y tabla de versiones — gestionado por la skill update-readme)
APP_VERSION = os.getenv("APP_VERSION", "1.14.0")

# ============================================================================
# SECURITY SETTINGS
# ============================================================================
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-secret-key-change-in-production")
EMAIL_SECRET = os.getenv("EMAIL_SECRET")

DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() in ("true", "1", "t")

# Validar que SECRET_KEY no sea el valor por defecto en producción
if not DEBUG and SECRET_KEY == "dev-secret-key-change-in-production":
    raise ValueError("⚠️  DJANGO_SECRET_KEY debe ser cambiado en producción!")

# Parse ALLOWED_HOSTS from comma-separated string
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# Security settings for production
if not DEBUG:
    # HTTPS/SSL
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "True").lower() == "true"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "True").lower() == "true"
    CSRF_COOKIE_SECURE = os.getenv("CSRF_COOKIE_SECURE", "True").lower() == "true"

    # HSTS (HTTP Strict Transport Security)
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))  # 1 año
    SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv("SECURE_HSTS_INCLUDE_SUBDOMAINS", "True").lower() == "true"
    SECURE_HSTS_PRELOAD = os.getenv("SECURE_HSTS_PRELOAD", "True").lower() == "true"

    # Otros headers de seguridad
    SECURE_CONTENT_TYPE_NOSNIFF = os.getenv("SECURE_CONTENT_TYPE_NOSNIFF", "True").lower() == "true"
    SECURE_BROWSER_XSS_FILTER = os.getenv("SECURE_BROWSER_XSS_FILTER", "True").lower() == "true"
    X_FRAME_OPTIONS = os.getenv("X_FRAME_OPTIONS", "DENY")

    # Trust the X-Forwarded-Proto header from reverse proxies (Nginx, Cloud Run LB)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    # CSRF Trusted Origins (para producción)
    csrf_origins = os.getenv("CSRF_TRUSTED_ORIGINS", "")
    if csrf_origins:
        CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in csrf_origins.split(",")]

# ============================================================================
# ENVIRONMENT
# ============================================================================
# DJANGO_ENV: "development" | "testing" | "production". Any other value is
# treated as development. Consumed by entrypoint.sh (collectstatic + teacher
# seeding only run for testing/production) and by the QA testing dashboard.
ENVIRONMENT = os.getenv("DJANGO_ENV", "development")

# QA testing tools — only enabled when DJANGO_ENV=testing (DEBUG off). The
# dashboard is then visible to logged-in ADMIN Teachers (see core.decorators).
IS_TESTING_ENV = ENVIRONMENT == "testing" and not DEBUG

# ============================================================================
# SESSION CONFIGURATION
# ============================================================================
SESSION_COOKIE_AGE = int(os.getenv("SESSION_COOKIE_AGE", "21600"))  # 6 horas
# Re-save the session on every request so the 6h window is INACTIVITY-based:
# any activity resets the timer, and 6h with no activity auto-logs-out.
SESSION_SAVE_EVERY_REQUEST = os.getenv("SESSION_SAVE_EVERY_REQUEST", "True").lower() == "true"
SESSION_COOKIE_HTTPONLY = os.getenv("SESSION_COOKIE_HTTPONLY", "True").lower() == "true"
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Strict" if not DEBUG else "Lax")

# ============================================================================
# SUPPORT / TICKETING
# ============================================================================
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", None)

# ============================================================================
# CSRF CONFIGURATION
# ============================================================================
CSRF_COOKIE_HTTPONLY = os.getenv("CSRF_COOKIE_HTTPONLY", "True" if not DEBUG else "False").lower() == "true"
CSRF_COOKIE_SAMESITE = os.getenv("CSRF_COOKIE_SAMESITE", "Strict" if not DEBUG else "Lax")

# Installed packages: httpx celery gspread pytest pandas markdown dotenv
INSTALLED_APPS = [  # https://www.djangoproject.com/
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",  # https://www.django-rest-framework.org/
    "corsheaders",  # https://github.com/adamchainz/django-cors-headers
    "django_filters",  # https://django-filter.readthedocs.io/en/main/
    "django_extensions",  # https://django-extensions.readthedocs.io/en/latest/
    # https://django-storages.readthedocs.io/en/latest/
    # https://github.com/jazzband/django-redis
    # https://django-environ.readthedocs.io/en/latest/
    "gsheets",  # https://pypi.org/project/django-gsheets/
    "core",
    "students",
    "billing",
    "comms",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # Debe ir después de SecurityMiddleware
    "core.middleware.NoHtmlCacheMiddleware",  # no-cache on dynamic HTML (fresh asset hashes)
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.QAErrorEmailMiddleware",  # QA: email errors to support
    "core.middleware.SimpleAuthMiddleware",  # Middleware de autenticación simple
    "core.audit_signals.AuditActorMiddleware",  # v1.10: attribute audit rows to the current user
]

ROOT_URLCONF = "project.urls"

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
                "core.context_processors.today_notifications",
            ],
        },
    },
]

WSGI_APPLICATION = "project.wsgi.application"

# ============================================================================
# DATABASE CONFIGURATION
# ============================================================================
# Two paths, both PostgreSQL:
#   1. DATABASE_URL  — Cloud Run via the Cloud SQL Unix socket (the URL ends
#                      with `?host=/cloudsql/PROJECT:REGION:INSTANCE` and has
#                      no hostname; dj_database_url handles that shape).
#   2. POSTGRES_*    — Docker (compose injects POSTGRES_HOST=db) or any other
#                      Postgres reachable by TCP.
# SQLite is intentionally not a fallback — the project always uses Postgres.
if database_url := os.getenv("DATABASE_URL", "").strip():
    DATABASES = {
        "default": dj_database_url.config(
            default=database_url,
            conn_max_age=600,
            conn_health_checks=True,
            ssl_require=not DEBUG,
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "fiveaday_db"),
            "USER": os.getenv("POSTGRES_USER", "fiveaday_user"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
            "HOST": os.getenv("POSTGRES_HOST", "localhost"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": 600,
            "OPTIONS": {"connect_timeout": 10},
        }
    }

# ============================================================================
# AUTHENTICATION
# ============================================================================
# Django's ModelBackend is the only backend — Teachers authenticate via their
# linked auth.User (email as username + hashed password). Dev environment and
# Google OAuth also go through this backend via get_or_create User + login().
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "es-es"

TIME_ZONE = "Europe/Madrid"

USE_I18N = True

DATE_FORMAT = "d/m/Y"
SHORT_DATE_FORMAT = "d/m/Y"
DATE_INPUT_FORMATS = ["%d/%m/%Y", "%Y-%m-%d"]

USE_TZ = True

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG" if DEBUG else "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": os.getenv("DJANGO_LOG_LEVEL", LOG_LEVEL),
            "propagate": False,
        },
        "core": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}

# ============================================================================
# STATIC AND MEDIA FILES
# ============================================================================
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR.parent / "staticfiles"

# Configuración de WhiteNoise para producción
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR.parent / "mediafiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ============================================================================
# EMAIL CONFIGURATION
# ============================================================================
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_SECRET", "")
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER

# ============================================================================
# CELERY CONFIGURATION
# ============================================================================
# Docker dev + testing VM: Redis broker (set by docker-compose.yml).
# Cloud Run: no broker — tasks run synchronously via CELERY_TASK_ALWAYS_EAGER
# (set automatically below when CELERY_BROKER_URL is unset).
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", None)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", None)

# Serialización
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

# Timezone
CELERY_TIMEZONE = "Europe/Madrid"
CELERY_ENABLE_UTC = True

# Configuración de tareas
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutos máximo por tarea

# Reintentos automáticos para tareas de email
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True

# Configuración de colas
CELERY_TASK_ROUTES = {
    "comms.tasks.send_*": {"queue": "emails"},
}

# Eager mode when no broker is configured (Cloud Run, tests, CI).
if not CELERY_BROKER_URL:
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True

# ============================================================================
# GOOGLE SHEETS INTEGRATION (v1.2)
# ============================================================================
# Optional. When both a service-account credential and a spreadsheet id are
# set, the /api/sheets/export/ endpoint and `manage.py export_to_sheets`
# command will write student/payment tables to that spreadsheet.
#
# One of these two must be set for auth (JSON inline wins if both are set):
GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON", "")
GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE", "")
# Target spreadsheet — the doc ID from its URL (…/spreadsheets/d/<ID>/edit).
# The service account must have Editor access to the sheet.
GOOGLE_SHEETS_SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "")

# ============================================================================
# TWILIO SMS (v1.8) — OPTIONAL
# ============================================================================
# All three must be set for the SMS service to be considered "configured".
# When any is missing, SmsService.is_configured() returns False and calls
# resolve to a structured failure so email fallback can kick in.
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")

# ============================================================================
# STRIPE PAYMENTS (v1.11) — OPTIONAL
# ============================================================================
# When STRIPE_SECRET_KEY is set the parent portal renders a "Pay now" button
# that creates a Checkout session. STRIPE_WEBHOOK_SECRET is the signing key
# for the /api/stripe/webhook/ receiver — required in production.
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
