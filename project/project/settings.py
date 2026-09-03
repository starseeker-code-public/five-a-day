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


def _env_int(name: str, default: int) -> int:
    """An int env var that survives sloppy values.

    `int(os.getenv(name, "N"))` bricks the whole process at settings import —
    gunicorn, migrate, every Cloud Run Job — the moment the var exists but is
    empty (a `KEY=` line left in .env, or a value blanked by --update-env-vars)
    or carries junk. A bad value here should mean "the default", loudly, not an
    academy-wide outage.
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        import logging

        logging.getLogger(__name__).error("Invalid integer for env %s; using default %d", name, default)
        return default


# ============================================================================
# SECURITY SETTINGS
# ============================================================================
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-secret-key-change-in-production")
# There is deliberately no `EMAIL_SECRET` SETTING. The env var of that name is
# the Gmail app password and is still read — once, by `EMAIL_HOST_PASSWORD` in
# the email section below, which is the only thing that ever needs it. Exposing
# it a second time as a Django setting put a live credential everywhere settings
# are enumerated: `django-admin diffsettings`, the debug 500 page's settings
# table, `manage.py shell` completions, and any future `/health/`-style dump.
# Django's own `SafeExceptionReporterFilter` masks it on the debug page (the key
# matches its `SECRET` regex), but nothing masked it in the other three, and
# nothing read it. Do not re-add it.

DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() in ("true", "1", "t")

# Validar que SECRET_KEY no sea el valor por defecto en producción
if not DEBUG and SECRET_KEY == "dev-secret-key-change-in-production":
    raise ValueError("⚠️  DJANGO_SECRET_KEY debe ser cambiado en producción!")

# Parse ALLOWED_HOSTS from comma-separated string. Each entry is stripped (the
# CSRF list below always was): "host1, host2" produced " host2", which matches
# no Host header, and that host answered 400 DisallowedHost while /health/ on
# the first one stayed green.
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

# Security settings for production
if not DEBUG:
    # HTTPS/SSL
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "True").lower() == "true"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "True").lower() == "true"
    CSRF_COOKIE_SECURE = os.getenv("CSRF_COOKIE_SECURE", "True").lower() == "true"

    # HSTS (HTTP Strict Transport Security)
    SECURE_HSTS_SECONDS = _env_int("SECURE_HSTS_SECONDS", 31536000)  # 1 año
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
SESSION_COOKIE_AGE = _env_int("SESSION_COOKIE_AGE", 21600)  # 6 horas
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

# Google Drive folder where expense receipts / justificantes live. Empty by
# default: the "Consultar recibos" buttons are HIDDEN when it is unset, rather
# than shipping a link to Drive's generic home page (the old placeholder).
GOOGLE_DRIVE_RECEIPTS_URL = os.getenv("GOOGLE_DRIVE_RECEIPTS_URL", "")

# ============================================================================
# RATE LIMITING
# ============================================================================
# How many reverse proxies sit in front of the app. `core.rate_limit` reads the
# client IP this many hops from the RIGHT of X-Forwarded-For, because a proxy
# APPENDS what it saw — everything to the left of our own hops is attacker
# controlled. Cloud Run and a single nginx are both 1. Use 0 when the app is
# reached directly, so X-Forwarded-For is ignored entirely.
#
# The DEFAULT is environment-aware: production sits behind Cloud Run's front end
# (exactly one trusted hop), but the QA VM and the dev stack bind Gunicorn
# straight to the interface with NO proxy — there, a default of 1 meant the
# bucket key was read from a header only the client wrote, so a rotating
# X-Forwarded-For gave every request a fresh window and defeated every
# credential throttle on an internet-exposed host.
TRUSTED_PROXY_COUNT = _env_int("TRUSTED_PROXY_COUNT", 1 if ENVIRONMENT == "production" else 0)

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

    # The CACHE BACKEND is a security control here, not a performance knob:
    # `core.rate_limit` is cache-backed, and Django's default LocMemCache is
    # PER-PROCESS. Neither branch above assigns CACHES when both CACHE_URL and
    # CACHE_DB are absent, so the name is simply undefined and Django falls back
    # to LocMemCache — silently, with every rate limit multiplied by
    # workers x instances (4 x maxScale 2 => "5 logins/minute" really up to 40).
    #
    # Dropping the var is easy to do by accident: `gcloud run deploy
    # --set-env-vars` REPLACES the entire env set, so one deploy that repeats
    # the wrong list unsets CACHE_DB and nothing anywhere reports it. Fail the
    # start-up instead — Cloud Run then keeps serving the previous revision.
    _cache_backend = (globals().get("CACHES", {}).get("default") or {}).get("BACKEND", "")
    if not _cache_backend or "locmem" in _cache_backend.lower():
        _posture_errors.append(
            "CACHE_URL or CACHE_DB must be set in production: a per-process LocMemCache "
            "multiplies every rate limit by workers x instances"
        )

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
    # `rest_framework`, `corsheaders` and `django_filters` were dropped from
    # INSTALLED_APPS in v1.23.0 and, as of v1.27.1, are no longer DEPENDENCIES
    # either — djangorestframework, django-cors-headers and django-filter are
    # out of pyproject.toml entirely. None of them was ever used: there is not a
    # single APIView, serializer or ViewSet in the tree, CorsMiddleware was
    # never added to MIDDLEWARE, and no filter backend was configured. Keeping
    # them installed-but-unused was pure CVE surface in the image and in
    # pip-audit — a DRF advisory already forced an unrelated release once.
    # Each was also a latent misconfiguration rather than a feature: DRF with no
    # REST_FRAMEWORK settings defaults every view to AllowAny, and adding
    # CorsMiddleware without CORS settings is one line away from relaxing
    # same-origin. Re-adding `rest_framework` means re-adding the dependency AND
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
    _default_db = dj_database_url.config(
        default=database_url,
        conn_max_age=600,
        conn_health_checks=True,
        ssl_require=not DEBUG,
    )
    # Same statement ceiling as the POSTGRES_* branch below. Set after
    # `config()` because dj_database_url has no argument for driver OPTIONS —
    # and it must MERGE into the OPTIONS `config()` built, never replace them:
    # for a Cloud SQL URL the Unix-socket path travels as OPTIONS["host"]
    # (ssl_require adds OPTIONS["sslmode"] too), so a dict-union that swapped
    # the whole OPTIONS in left HOST empty and psycopg2 dialing the default
    # local socket — which took down the v1.26.2 production migrate while the
    # POSTGRES_* branch (dev, testing VM) worked fine.
    _default_db.setdefault("OPTIONS", {})["options"] = (
        f"-c statement_timeout={os.getenv('DB_STATEMENT_TIMEOUT_MS', '30000')}"
    )
    # `connect_timeout` existed only on the POSTGRES_* branch — the third time
    # these two have drifted (CONN_HEALTH_CHECKS was the first, statement_timeout
    # the second), and the drift always favoured the branch that is easy to test
    # locally over the one PRODUCTION actually runs. `statement_timeout` does not
    # help here: it bounds a statement on an ESTABLISHED connection, and says
    # nothing about a TCP connect that never completes. Without this, a stalled
    # Cloud SQL socket parks a Gunicorn worker on the OS default connect timeout
    # (minutes), and with 4 workers the service stops answering.
    _default_db["OPTIONS"]["connect_timeout"] = _env_int("DB_CONNECT_TIMEOUT", 10)
    DATABASES = {"default": _default_db}
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
                # Same env var as the DATABASE_URL branch above, so the two
                # cannot drift again.
                "connect_timeout": _env_int("DB_CONNECT_TIMEOUT", 10),
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

# Parent-portal passwords are validated against THIS list, not the one above.
# `AUTH_PASSWORD_VALIDATORS` is tuned for staff accounts — they are effectively
# superusers over a database of minors' personal data, so 12 characters is a
# deliberate, defensible cost. A family reaches a read-only view of their own
# children and their own invoices; holding them to the staff bar buys almost
# nothing and costs onboarding, which for this academy means phone calls.
#
# Django's own defaults, minus the similarity check (which compares the password
# against `user.username / first_name / last_name / email` and is a no-op here
# anyway, because the portal validates before it has anything to compare to).
# The floor is 8, not lower: a family portal still exposes payment history.
PARENT_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
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
# ERROR ALERTING
# ============================================================================
# Until v1.27.1 there was NO application-error alerting of any kind: no Sentry,
# no ADMINS, no log-based alert. A recurring 500, or a Cloud Run Job failing
# every night, was invisible until a user phoned the academy — and the Jobs are
# where the money is (generate_payments, payment reminders, the monthly report).
#
# What was chosen, and why it is this and not Sentry: Gmail SMTP is already
# configured, already used for every transactional mail, and already the channel
# the owners read. `ADMINS` + Django's own `AdminEmailHandler` therefore costs
# one setting and no new dependency, no new account and no new egress path — and
# a paid SaaS for 3-10 users watching one Cloud Run service is not proportionate.
# The trade-off accepted: no aggregation, no release tracking, no breadcrumbs.
# If that becomes the binding constraint, a log-based alert on Cloud Logging
# (already collecting stdout) is the next step up, still without a dependency.
#
# Defaults to SUPPORT_EMAIL, which production already sets — so alerting turns
# on with no env change. `DJANGO_ADMINS` overrides with a comma-separated list.
_admin_emails = [a.strip() for a in os.getenv("DJANGO_ADMINS", "").split(",") if a.strip()]
if not _admin_emails and SUPPORT_EMAIL:
    _admin_emails = [SUPPORT_EMAIL]
ADMINS = [("Five a Day", address) for address in _admin_emails]
MANAGERS = ADMINS
# Django's default is "[Django] ", which reads like somebody else's alert.
EMAIL_SUBJECT_PREFIX = "[Five a Day] "

# Two guards, because error mail is exactly the feature that turns into noise:
#   1. PRODUCTION ONLY. Keyed on ENVIRONMENT, like the posture guard — the QA VM
#      has QAErrorEmailMiddleware for this (with body redaction), development
#      has a console, and the TEST SUITE must never mail: EMAIL_BACKEND is
#      locmem there, so an alert would land in `mail.outbox` and break every
#      test that counts messages.
#   2. THROTTLED. `core.rate_limit.ErrorAlertThrottleFilter` — one mail per
#      logging site per 15 min, so a single transient error cannot spam and a
#      loop of failures cannot mute the inbox.
_ERROR_MAIL_ENABLED = ENVIRONMENT == "production" and bool(ADMINS)

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================
# Normalised + validated: dictConfig raises on an unknown level, and a
# well-meaning `LOG_LEVEL=info` (lowercase) prevented the whole app from booting.
LOG_LEVEL = (os.getenv("LOG_LEVEL") or ("DEBUG" if DEBUG else "INFO")).strip().upper()
if LOG_LEVEL not in ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"):
    LOG_LEVEL = "DEBUG" if DEBUG else "INFO"

_LOG_HANDLERS = ["console", "mail_admins"] if _ERROR_MAIL_ENABLED else ["console"]

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
    "filters": {
        "require_debug_false": {"()": "django.utils.log.RequireDebugFalse"},
        "throttle_error_alerts": {"()": "core.rate_limit.ErrorAlertThrottleFilter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        # Only reachable when _ERROR_MAIL_ENABLED put it in _LOG_HANDLERS; the
        # entry itself is harmless (dictConfig instantiates it either way, and
        # AdminEmailHandler with no ADMINS sends nothing).
        "mail_admins": {
            "level": "ERROR",
            "class": "django.utils.log.AdminEmailHandler",
            "filters": ["require_debug_false", "throttle_error_alerts"],
            # `include_html` would attach the full technical 500 page. The body
            # already carries the traceback and the redacted POST via
            # DEFAULT_EXCEPTION_REPORTER_FILTER; the HTML version adds every
            # local variable in every frame to an inbox, which for this app
            # means student rows and payment amounts.
            "include_html": False,
        },
    },
    "root": {
        "handlers": _LOG_HANDLERS,
        "level": LOG_LEVEL,
    },
    "loggers": {
        # `django.request` (where an unhandled 500 is logged) propagates to
        # `django`, and this logger has propagate=False — so before v1.27.1 the
        # only handler a 500 ever reached was the console.
        "django": {
            "handlers": _LOG_HANDLERS,
            "level": os.getenv("DJANGO_LOG_LEVEL", LOG_LEVEL),
            "propagate": False,
        },
        # Where every `logger.exception(...)` in core/views lives.
        "core": {
            "handlers": _LOG_HANDLERS,
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}

# Governs the debug 500 page AND the body of every AdminEmailHandler mail.
# Django only cleanses a request body when the view used
# @sensitive_post_parameters, and none of this app's hand-rolled auth views do —
# so without this an error report from /login/ or /api/password-change/ carries
# the submitted password in cleartext. Shares one field-name list with
# QAErrorEmailMiddleware.
DEFAULT_EXCEPTION_REPORTER_FILTER = "core.middleware.RedactingExceptionReporterFilter"

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
# Backend is env-overridable so a non-production environment can opt OUT of real
# SMTP. The QA/testing VM runs the full Celery Beat schedule (birthday, payment
# reminders, monthly report, Fun Friday) and, hard-wired to Gmail, would
# autonomously mail whatever addresses its database holds — a real hazard the
# moment a production dump is restored onto it. Set
# EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend (or .dummy) in
# .env.testing to neutralise that; production leaves it at the SMTP default.
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = _env_int("EMAIL_PORT", 587)
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() in ("true", "1", "t")
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_SECRET", "")
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER
# From address for error mail (AdminEmailHandler). Django's default is
# "root@localhost", which Gmail's SMTP refuses outright — so the alerting would
# have looked configured and delivered nothing.
SERVER_EMAIL = os.getenv("SERVER_EMAIL", "") or DEFAULT_FROM_EMAIL
# A hung SMTP socket must not park a Gunicorn worker forever. Without this,
# smtplib inherits the OS default connect timeout (minutes), and the mass-mail
# views send synchronously in the request — one blackholed port 587 could wedge
# a worker until it was killed. 20 s is generous for Gmail.
EMAIL_TIMEOUT = _env_int("EMAIL_TIMEOUT", 20)

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
# GCP BILLING EXPORT — OPTIONAL
# ============================================================================
# Actual Google Cloud spend for the QA dashboard's "Gastos GCP" line and the
# automated monthly "Software" expense (billing/services/gcp_cost_service.py).
# GCP only exposes real costs through the standard billing export to BigQuery,
# so this needs that export enabled and points at its table:
#   "project.dataset.gcp_billing_export_v1_XXXXXX"
# Unset ⇒ the feature is off (the UI shows "—" and nothing is archived).
GCP_BILLING_EXPORT_TABLE = os.getenv("GCP_BILLING_EXPORT_TABLE", "")
# Project the BigQuery query job runs under (needs BigQuery Job User on it).
# Defaults to the export table's own project.
GCP_BILLING_PROJECT_ID = os.getenv("GCP_BILLING_PROJECT_ID", "")
# Optional `project.id` filter for billing accounts covering several projects.
GCP_BILLING_PROJECT_FILTER = os.getenv("GCP_BILLING_PROJECT_FILTER", "")
# Dedicated credential (JSON inline wins). When both are unset the service
# falls back to the Google Sheets service account, then to Application Default
# Credentials (the attached service account on the VM / Cloud Run).
GCP_BILLING_SERVICE_ACCOUNT_JSON = os.getenv("GCP_BILLING_SERVICE_ACCOUNT_JSON", "")
GCP_BILLING_SERVICE_ACCOUNT_FILE = os.getenv("GCP_BILLING_SERVICE_ACCOUNT_FILE", "")

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
