#!/bin/sh
# ============================================================================
# ENTRYPOINT — Five a Day
# ============================================================================
# Runs the same boot sequence everywhere (Docker dev, testing VM, Cloud Run).
# Behaviour is driven entirely by env vars:
#   - DATABASE_URL set       → Cloud SQL via socket, skip the wait-for-Postgres
#   - DATABASE_URL unset     → TCP Postgres (Docker), wait until it answers
#   - DJANGO_ENV != dev      → collectstatic + seed_teachers run
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
        echo "⏭️  Skipping migrations + collectstatic + seed_teachers (Celery container)"
        ;;
    *)
        echo "📦 Applying database migrations..."
        python project/manage.py migrate --noinput

        if [ "$DJANGO_ENV" = "testing" ] || [ "$DJANGO_ENV" = "production" ]; then
            echo "📁 Collecting static files..."
            python project/manage.py collectstatic --noinput --clear

            echo "🧑‍🏫 Seeding teachers from TEACHER_SEED_* env vars..."
            python project/manage.py seed_teachers || echo "⚠️  seed_teachers reported an issue (non-fatal)"
        fi
        ;;
esac

echo "✨ Initialization complete!"
exec "$@"
