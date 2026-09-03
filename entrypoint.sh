#!/bin/sh
# ============================================================================
# ENTRYPOINT — Five a Day
# ============================================================================
# Runs the same boot sequence everywhere (Docker dev, testing VM, Cloud Run).
# Behaviour is driven entirely by env vars:
#   - DATABASE_URL set       → Cloud SQL via socket, skip the wait-for-Postgres
#   - DATABASE_URL unset     → TCP Postgres (Docker), wait until it answers
#                              (BOUNDED: DB_WAIT_MAX_ATTEMPTS × 2 s, then abort)
#   - DJANGO_ENV != dev      → collectstatic runs
#   - Always                 → seed_teachers + seed_enrollment_types (idempotent)
#   - DJANGO_ENV != production → seed_demo_parents (portal demo data)
#   - Always                 → migrate, then exec the Dockerfile CMD (gunicorn)
#
# Failure policy: everything OPTIONAL logs a warning and continues
# (createcachetable, seed_teachers, seed_demo_parents). The two things the app
# cannot run without are FATAL — migrate, and seed_enrollment_types, without
# whose four rows no student of any kind can be enrolled.
# ============================================================================

set -e

mkdir -p /app/logs /app/staticfiles /app/mediafiles

# ── Wait for PostgreSQL (TCP / Docker case only) ────────────────────────────
# BOUNDED. The loop used to be `until psql …; do sleep 2; done` with no cap, so
# a wrong password, a wrong POSTGRES_HOST or a DB that never comes up left the
# container "starting" forever: no logs after the first line, no crash, nothing
# for `restart: unless-stopped` to react to, and `docker compose up` hanging on
# a service that will never be healthy. Failing fast turns that into a visible
# exit code — Compose reports it and Cloud Run keeps the previous revision.
# DB_WAIT_MAX_ATTEMPTS × 2 s is the budget (default 60 → 2 min, comfortably more
# than the postgres:16-alpine container needs on the e2-micro).
if [ -z "$DATABASE_URL" ]; then
    _DB_WAIT_MAX=${DB_WAIT_MAX_ATTEMPTS:-60}
    _DB_WAIT_N=0
    echo "⏳ Waiting for PostgreSQL at ${POSTGRES_HOST:-localhost}:${POSTGRES_PORT:-5432} (max ${_DB_WAIT_MAX} attempts)..."
    until PGPASSWORD="$POSTGRES_PASSWORD" psql \
        -h "${POSTGRES_HOST:-localhost}" \
        -p "${POSTGRES_PORT:-5432}" \
        -U "$POSTGRES_USER" \
        -d "$POSTGRES_DB" \
        -c '\q' 2>/dev/null; do
        _DB_WAIT_N=$((_DB_WAIT_N + 1))
        if [ "$_DB_WAIT_N" -ge "$_DB_WAIT_MAX" ]; then
            echo "❌ PostgreSQL at ${POSTGRES_HOST:-localhost}:${POSTGRES_PORT:-5432} did not answer after $((_DB_WAIT_MAX * 2))s"
            echo "   (db=${POSTGRES_DB:-<unset>} user=${POSTGRES_USER:-<unset>}) — check the host, the credentials"
            echo "   and that the db service is healthy. Aborting instead of hanging forever."
            exit 1
        fi
        echo "   …still waiting (${_DB_WAIT_N}/${_DB_WAIT_MAX})"
        sleep 2
    done
    echo "✅ PostgreSQL is available"
fi

# ── Migrations (skip for Celery workers — they should never migrate) ────────
FIRST_ARG="$1"
case "$FIRST_ARG" in
    celery)
        echo "⏭️  Skipping migrations + collectstatic + seeding (Celery container)"
        ;;
    *)
        # RUN_MIGRATIONS_ON_START=false lets a deploy pipeline that OWNS
        # migrations (backup → repoint jobs → fiveaday-migrate → roll service)
        # stop the Cloud Run *service* from self-migrating on every cold start,
        # which otherwise bypassed that ordering and its pre-deploy backup. Left
        # unset it defaults to true, so dev and the testing VM are unchanged.
        _RUN_MIGRATIONS=$(printf '%s' "${RUN_MIGRATIONS_ON_START:-true}" | tr '[:upper:]' '[:lower:]')
        if [ "$_RUN_MIGRATIONS" = "true" ] || [ "$_RUN_MIGRATIONS" = "1" ] || [ "$_RUN_MIGRATIONS" = "t" ]; then
            echo "📦 Applying database migrations..."
            python project/manage.py migrate --noinput
        else
            echo "⏭️  Skipping migrations (RUN_MIGRATIONS_ON_START=$RUN_MIGRATIONS_ON_START) — pipeline owns them"
        fi

        # Cache table for CACHE_DB (the DatabaseCache backend). Idempotent — it
        # prints "already exists" and exits 0 on every boot after the first,
        # which is why it lives here rather than in a migration. The rate
        # limiter is cache-backed, so without a SHARED cache the login throttle
        # is per-Gunicorn-worker and per-instance; see settings.py CACHES.
        #
        # Parsed case-insensitively to MATCH settings.py, which accepts
        # true/1/t/TRUE/… — the old exact-literal check (`= "True"`) meant
        # CACHE_DB=TRUE selected DatabaseCache in Django while skipping the table
        # here, so every cache.add() raised and the limiter failed open silently.
        _CACHE_DB=$(printf '%s' "${CACHE_DB:-false}" | tr '[:upper:]' '[:lower:]')
        if [ "$_CACHE_DB" = "true" ] || [ "$_CACHE_DB" = "1" ] || [ "$_CACHE_DB" = "t" ]; then
            echo "🗃️  Ensuring the database cache table exists..."
            python project/manage.py createcachetable || echo "⚠️  createcachetable reported an issue (non-fatal)"
        fi

        if [ "$DJANGO_ENV" = "testing" ] || [ "$DJANGO_ENV" = "production" ]; then
            echo "📁 Collecting static files..."
            python project/manage.py collectstatic --noinput --clear
        fi

        # Teacher seeding runs in DEVELOPMENT too. It is a no-op without
        # TEACHER_SEED_* vars, and where they are set it is the only way to log
        # in locally as a NON-ADMIN teacher — the env-var admin login in
        # `login_view` always mints a superuser, so the trimmed non-admin UI and
        # NON_ADMIN_ALLOWED_URL_NAMES were untestable outside the QA VM.
        echo "🧑‍🏫 Seeding teachers from TEACHER_SEED_* env vars..."
        python project/manage.py seed_teachers || echo "⚠️  seed_teachers reported an issue (non-fatal)"

        # Reference data, not test data: without these four rows
        # EnrollmentService._resolve_enrollment_type raises and NO student of any
        # kind can be enrolled (Enrollment.enrollment_type is a non-null PROTECT
        # FK). Idempotent, so it runs every boot.
        #
        # FATAL on purpose — the one seed that is not optional. It used to end in
        # `|| echo "(non-fatal)"`, which is how production once served for weeks
        # with `0 Tipos de matrícula`: the boot log carried a warning nobody
        # read, /health/ was green, and the breakage only surfaced when somebody
        # opened the admin. A container that cannot enrol a student is not
        # serving, so let it fail: Compose surfaces the exit code and Cloud Run
        # keeps the previous revision instead of promoting a broken one.
        echo "🎓 Ensuring enrollment types exist..."
        python project/manage.py seed_enrollment_types

        # Parent-portal demo data. Refused outright in production by the
        # command itself (and the portal's password login is refused there too),
        # so this is a belt-and-braces guard, not the only one.
        if [ "$DJANGO_ENV" != "production" ]; then
            echo "👪 Seeding demo parents from DEMO_PARENT_* env vars..."
            python project/manage.py seed_demo_parents || echo "⚠️  seed_demo_parents reported an issue (non-fatal)"
        fi
        ;;
esac

echo "✨ Initialization complete!"
exec "$@"
