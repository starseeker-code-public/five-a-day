# ============================================================================
# DOCKERFILE - Five a Day Django Application
# ============================================================================
# Multi-stage build: builder installs dependencies with UV, runtime is lean.

# ============================================================================
# STAGE 1: Builder - Install dependencies with UV
# ============================================================================
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install system deps needed to compile Python packages
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    postgresql-client \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install UV from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first (Docker layer caching)
COPY pyproject.toml uv.lock ./

# Install production dependencies into .venv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev --no-install-project

# ============================================================================
# STAGE 2: Runtime - Lean production image
# ============================================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=project.settings \
    PATH="/app/.venv/bin:$PATH"

# Install only runtime system deps
# git: used by the QA testing dashboard to show the last commit (branch, hash,
#      author, date) — see core/views/testing_tools._git_info.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    libpq-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user. UID and GID are pinned so the numeric `USER 1000:1000`
# below is unambiguous (hadolint DL3066 - a name-based USER can't be resolved by
# the host, which matters for Cloud Run / K8s runAsNonRoot checks).
RUN groupadd -g 1000 django && \
    useradd -m -u 1000 -g 1000 django && \
    mkdir -p /app /app/staticfiles /app/mediafiles && \
    chown -R django:django /app

WORKDIR /app

# Copy UV and the virtual environment from builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY --from=builder --chown=django:django /app/.venv /app/.venv

# Copy application code
COPY --chown=django:django . .

# Copy and set permissions on entrypoint
COPY --chown=django:django entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Switch to non-root user - numeric to satisfy hadolint DL3066.
# 1000:1000 is the django user/group created in the runtime stage above.
USER 1000:1000

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]

# Default: Gunicorn (production)
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "120", "project.wsgi:application"]
