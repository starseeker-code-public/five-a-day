import os
from pathlib import Path
from typing import Any

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
# pyproject.toml is the SINGLE SOURCE OF TRUTH for the version. This file used to
# carry a hand-maintained copy that `make version` kept in sync with sed, and the
# copy silently lagged when a release edited one and not the other (v1.20.0 shipped
# with the bump missing and needed a follow-up commit). Reading it instead means
# there is nothing here to forget.
#
# `importlib.metadata` is NOT usable: the Docker build runs
# `uv sync --no-install-project`, so the app is never installed as a distribution
# and there is no package metadata to read. The Dockerfile's `COPY . .` does put
# pyproject.toml at /app/pyproject.toml, one level above BASE_DIR (/app/project).
def _version_from_pyproject() -> str:
    """Read `[project] version` from pyproject.toml, or "unknown" if unreadable.

    Deliberately fails LOUD rather than falling back to a hard-coded number: a
    stale literal is indistinguishable from a correct one, whereas "unknown" on
    /health/ says plainly that the deploy cannot see its own pyproject.toml.
    """
    try:
        import tomllib

        with open(BASE_DIR.parent / "pyproject.toml", "rb") as handle:
            return str(tomllib.load(handle)["project"]["version"])
    except (OSError, KeyError, ValueError):
        return "unknown"


# The env var still wins, so Cloud Run can pin or override a version without a
# rebuild. See CLAUDE.md — a legacy APP_VERSION line in .env silently overrides.
APP_VERSION = os.getenv("APP_VERSION") or _version_from_pyproject()

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

# Content-Security-Policy: report-only until this is turned on.
# Defined OUTSIDE the `not DEBUG` block above because SecurityHeadersMiddleware
# runs in every environment — the whole point of report-only mode is to collect
# violations in development before enforcing anywhere. The middleware reads it
# with getattr(settings, "CSP_ENFORCE", False), so leaving it unassigned here
# silently pinned CSP to report-only and made the documented env var a no-op.
CSP_ENFORCE = os.getenv("CSP_ENFORCE", "False").lower() in ("true", "1", "t")

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

# Shared secret for the /health/?deep=1 data fingerprint. /health/ is public, so
# row counts are only returned when the caller presents this token in the
# X-Probe-Token header. Unset (the default) means the deep probe still reports
# connectivity and migration state, just not the counts. Deploy tooling uses the
# counts to prove a release did not land on the wrong database.
HEALTH_PROBE_TOKEN = os.getenv("HEALTH_PROBE_TOKEN", "")

# ============================================================================
# SESSION CONFIGURATION
# ============================================================================
SESSION_COOKIE_AGE = int(os.getenv("SESSION_COOKIE_AGE", "21600"))  # 6 horas
# Re-save the session on every request so the 6h window is INACTIVITY-based:
# any activity resets the timer, and 6h with no activity auto-logs-out.
SESSION_SAVE_EVERY_REQUEST = os.getenv("SESSION_SAVE_EVERY_REQUEST", "True").lower() == "true"
SESSION_COOKIE_HTTPONLY = os.getenv("SESSION_COOKIE_HTTPONLY", "True").lower() == "true"
# Lax, not Strict, in every environment. The Google OAuth callback is a cross-site
# top-level navigation (accounts.google.com → our domain); under Strict the browser
# withholds the session cookie on that hop, so `google_oauth_state` is missing when
# google_oauth_callback compares it and every login dies with "Estado OAuth inválido".
# Lax still blocks the cookie on cross-site POSTs and subresource requests, which is
# where the CSRF risk actually lives. It also means arriving from an external link
# (a payment-reminder email) no longer shows the teacher as logged out.
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")

# ============================================================================
# SUPPORT / TICKETING
# ============================================================================
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", None)

# ============================================================================
# RATE LIMITING
# ============================================================================
# How many reverse proxies sit in front of the app. `core.rate_limit` reads the
# client IP this many hops from the RIGHT of X-Forwarded-For, because a proxy
# APPENDS what it saw — everything to the left of our own hops is attacker
# controlled. Cloud Run and a single nginx are both 1. Use 0 when the app is
# reached directly, so X-Forwarded-For is ignored entirely.
TRUSTED_PROXY_COUNT = int(os.getenv("TRUSTED_PROXY_COUNT", "1"))

# The rate limiter is backed by the cache, so the cache backend decides whether
# the limit is real. Django's default LocMemCache is PER-PROCESS: with Gunicorn's
# 4 workers the login throttle was 4x what is configured, and on Cloud Run it
# multiplied again per instance (maxScale 2 → up to 8 independent counters, so
# "5 per minute" was really up to 40).
#
# Precedence:
#   1. CACHE_URL   — Redis. Preferred when one is reachable.
#   2. CACHE_DB=1  — the PostgreSQL cache table. Shared across every worker and
#                    every instance, and adds no new infrastructure: sessions are
#                    already database-backed, so the DB is on the request path
#                    regardless. This is what production uses — Memorystore plus
#                    the VPC connector it requires costs more per month than the
#                    entire rest of the stack, for 3-10 users.
#   3. neither     — LocMemCache. Fine for development and the test suite.
#
# `createcachetable` is idempotent and runs from entrypoint.sh, so option 2 needs
# no migration. Do NOT point CACHE_URL at an unreachable host: `core.rate_limit`
# calls `cache.add()` without a fallback, so an unreachable cache turns every
# login into a 500.
# Declared before the branches so mypy does not infer the value type from
# whichever one it reads first. The Redis branch is all-string, so adding
# `OPTIONS` (a nested dict) to the DatabaseCache branch below made it a
# `dict-item` error. `Any` is what django-stubs uses for this setting.
CACHES: dict[str, dict[str, Any]]
if cache_url := os.getenv("CACHE_URL", "").strip():
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": cache_url}}
elif os.getenv("CACHE_DB", "False").lower() in ("true", "1", "t"):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": os.getenv("CACHE_DB_TABLE", "django_cache"),
            # Django's default MAX_ENTRIES is 300, and DatabaseCache culls a third
            # of the table once it is exceeded. The rate limiter's key space is
            # tiny (one slot per client per window per action, for 3-10 users), so
            # the default is never reached today — stated explicitly so that adding
            # a second consumer of this cache does not silently start evicting
            # throttle counters, which would open the login rate limit.
            "OPTIONS": {"MAX_ENTRIES": 5000},
        }
    }

# ============================================================================
# CSRF CONFIGURATION
# ============================================================================
CSRF_COOKIE_HTTPONLY = os.getenv("CSRF_COOKIE_HTTPONLY", "True" if not DEBUG else "False").lower() == "true"
CSRF_COOKIE_SAMESITE = os.getenv("CSRF_COOKIE_SAMESITE", "Strict" if not DEBUG else "Lax")

# ============================================================================
# PRODUCTION POSTURE GUARD
# ============================================================================
# Every security setting above is `os.getenv(..., "True")` — overridable, which
# is what lets .env.testing legitimately run the QA VM over plain HTTP. The cost
# is that a typo, a stale .env line, or a value copied across from
# .env.testing silently disables HSTS or ships non-Secure session cookies in
# PRODUCTION, and nothing in code, tests, CI or the deploy pipeline notices.
#
# So assert the non-negotiables when DJANGO_ENV=production, the same way
# DJANGO_SECRET_KEY is already asserted. This is deliberately a hard failure:
# Cloud Run keeps serving the previous revision when a new one cannot start, so
# a misconfigured deploy stalls loudly instead of quietly downgrading the
# security posture of a live system holding minors' personal data.
#
# `testing` and `development` are exempt by design — do not "simplify" this by
# keying it on `not DEBUG`, which would break the testing VM.
if ENVIRONMENT == "production":
    _posture_errors = []

    if DEBUG:
        _posture_errors.append("DJANGO_DEBUG must be False in production")
    if "*" in ALLOWED_HOSTS:
        _posture_errors.append("DJANGO_ALLOWED_HOSTS must not contain '*' in production")
    for _name in ("SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE", "SECURE_SSL_REDIRECT"):
        # These live inside the `if not DEBUG` block above, so a production run
        # with DEBUG=True leaves them undefined — hence the getattr default.
        if not globals().get(_name, False):
            _posture_errors.append(f"{_name} must be True in production")
    if not SESSION_COOKIE_HTTPONLY:
        _posture_errors.append("SESSION_COOKIE_HTTPONLY must be True in production")
    if globals().get("SECURE_HSTS_SECONDS", 0) < 31536000:
        _posture_errors.append("SECURE_HSTS_SECONDS must be at least 31536000 (1 year) in production")

    if _posture_errors:
        raise ValueError(
            "⚠️  Insecure production configuration — refusing to start:\n  - " + "\n  - ".join(_posture_errors)
        )
    del _posture_errors

# Installed packages: httpx celery gspread pytest pandas markdown dotenv
INSTALLED_APPS = [  # https://www.djangoproject.com/
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # `rest_framework`, `corsheaders` and `django_filters` were REMOVED (v1.23.0).
    # None of them was used: there is not a single APIView, serializer or
    # ViewSet in the tree, CorsMiddleware was never added to MIDDLEWARE, and no
    # filter backend is configured. Each was a latent misconfiguration rather
    # than a feature — DRF with no REST_FRAMEWORK settings defaults every view
    # to AllowAny, and adding CorsMiddleware without CORS settings is one line
    # away from relaxing same-origin. Re-adding `rest_framework` REQUIRES
    # setting DEFAULT_PERMISSION_CLASSES to IsAuthenticated in the same commit.
    "django_extensions",  # https://django-extensions.readthedocs.io/en/latest/
    # https://django-storages.readthedocs.io/en/latest/
    # https://github.com/jazzband/django-redis
    # https://django-environ.readthedocs.io/en/latest/
    #
    # `gsheets` (django-gsheets) is deliberately NOT enabled. Nothing imports it
    # today — the Sheets export uses `gspread` directly via
    # core.services.google_sheets_service — and having it in INSTALLED_APPS made
    # `makemigrations --check` fail: DEFAULT_AUTO_FIELD=BigAutoField wants an
    # AlterField migration written into the package's own site-packages
    # directory, which is not a file we can commit.
    #
    # The dependency stays in pyproject.toml on purpose (planned future use).
    # To turn it back on: add "gsheets" below AND give it an AppConfig with
    # `default_auto_field = "django.db.models.AutoField"` so the drift doesn't
    # return. Its existing migrations are already applied, so the table is
    # still there and re-enabling needs no data work.
    "core",
    "students",
    "billing",
    "comms",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # Debe ir después de SecurityMiddleware
    "core.middleware.NoHtmlCacheMiddleware",  # no-cache on dynamic HTML (fresh asset hashes)
    "core.middleware.SecurityHeadersMiddleware",  # CSP + Permissions-Policy
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
                "core.context_processors.csp_nonce",
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
        | {
            # Same statement ceiling as the POSTGRES_* branch below. Set after
            # `config()` because dj_database_url has no argument for driver
            # OPTIONS, and this is the branch production actually runs on.
            "OPTIONS": {"options": f"-c statement_timeout={os.getenv('DB_STATEMENT_TIMEOUT_MS', '30000')}"},
        }
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
            # `CONN_HEALTH_CHECKS` must accompany a non-zero CONN_MAX_AGE. The
            # DATABASE_URL branch above passes `conn_health_checks=True`; this one
            # did not, so it kept a connection for 10 minutes and never checked it
            # was still alive. Postgres restarting, or a proxy dropping an idle
            # socket, then surfaced as an OperationalError on the FIRST query of an
            # unlucky request — served by the branch the testing VM and every dev
            # container use. Django re-opens the connection instead when this is on.
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {
                "connect_timeout": 10,
                # A ceiling on any single statement. There is no connection pooler
                # in front of Cloud SQL and the instance's connection limit is
                # small, so one pathological query (a missing predicate, an
                # accidental cross join) could otherwise hold a connection
                # indefinitely and starve the rest. 30s is far above every measured
                # query in the app — the slowest page is a bounded aggregate.
                #
                # It applies to MIGRATIONS too, per statement: an `AddIndex` on a
                # large table is a single statement and would abort at the ceiling.
                # At this academy's ceiling (~2,000 students, so tens of thousands
                # of payment rows) an index build is sub-second, but raise
                # `DB_STATEMENT_TIMEOUT_MS` for the run if a future migration
                # rewrites a big table.
                "options": f"-c statement_timeout={os.getenv('DB_STATEMENT_TIMEOUT_MS', '30000')}",
            },
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
        # Django's default is 8. These accounts are effectively superusers over a
        # database of minors' personal data, and the login throttle is per-IP
        # with no lockout, so the password is the whole wall.
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
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

# The three settings above are IGNORED on their own: localization always takes
# precedence, so `LANGUAGE_CODE = "es-es"` made Django read
# django.conf.locale.es.formats and render every unfiltered date as
# "31 de agosto de 2026". FORMAT_MODULE_PATH is the only supported override
# since USE_L10N was removed in Django 5 — project/formats/es/formats.py
# restores dd/mm/yyyy across templates, emails and the admin.
FORMAT_MODULE_PATH = "project.formats"

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

# Eager mode when no broker is configured (Cloud Run, tests, CI). Assigned
# unconditionally — Celery reads these via `config_from_object`, so they must
# exist as plain module-level settings either way.
CELERY_TASK_ALWAYS_EAGER = not CELERY_BROKER_URL
CELERY_TASK_EAGER_PROPAGATES = not CELERY_BROKER_URL

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
