#!/bin/sh
# ============================================================================
# ENTRYPOINT — Five a Day
# ============================================================================
# Runs the same boot sequence everywhere (Docker dev, testing VM, Cloud Run).
# Behaviour is driven entirely by env vars:
#   - DATABASE_URL set       → Cloud SQL via socket, skip the wait-for-Postgres
#   - DATABASE_URL unset     → TCP Postgres (Docker), wait until it answers
#   - DJANGO_ENV != dev      → collectstatic + seed_teachers + seed_enrollment_types run
#   - Always                 → migrate, then exec the Dockerfile CMD (gunicorn)
# ============================================================================

set -e

mkdir -p /app/logs /app/staticfiles /app/mediafiles

# ── Wait for PostgreSQL (TCP / Docker case only) ────────────────────────────
if [ -z "$DATABASE_URL" ]; then
    echo "⏳ Waiting for PostgreSQL at ${POSTGRES_HOST:-localhost}:${POSTGRES_PORT:-5432}..."
    until PGPASSWORD="$POSTGRES_PASSWORD" psql \
        -h "${POSTGRES_HOST:-localhost}" \
        -p "${POSTGRES_PORT:-5432}" \
        -U "$POSTGRES_USER" \
        -d "$POSTGRES_DB" \
        -c '\q' 2>/dev/null; do
        echo "   …still waiting"
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
        echo "📦 Applying database migrations..."
        python project/manage.py migrate --noinput

        # Cache table for CACHE_DB=1 (the DatabaseCache backend). Idempotent —
        # it prints "already exists" and exits 0 on every boot after the first,
        # which is why it lives here rather than in a migration. The rate
        # limiter is cache-backed, so without a SHARED cache the login throttle
        # is per-Gunicorn-worker and per-instance; see settings.py CACHES.
        if [ "${CACHE_DB:-False}" = "True" ] || [ "${CACHE_DB:-false}" = "true" ] || [ "${CACHE_DB:-0}" = "1" ]; then
            echo "🗃️  Ensuring the database cache table exists..."
            python project/manage.py createcachetable || echo "⚠️  createcachetable reported an issue (non-fatal)"
        fi

        if [ "$DJANGO_ENV" = "testing" ] || [ "$DJANGO_ENV" = "production" ]; then
            echo "📁 Collecting static files..."
            python project/manage.py collectstatic --noinput --clear

            echo "🧑‍🏫 Seeding teachers from TEACHER_SEED_* env vars..."
            python project/manage.py seed_teachers || echo "⚠️  seed_teachers reported an issue (non-fatal)"
            # Reference data, not test data: without these rows EnrollmentService
            # raises and no student can be enrolled. Idempotent, so it runs every boot.
            echo "🎓 Ensuring enrollment types exist..."
            python project/manage.py seed_enrollment_types || echo "⚠️  seed_enrollment_types reported an issue (non-fatal)"
        fi
        ;;
esac

echo "✨ Initialization complete!"
exec "$@"
