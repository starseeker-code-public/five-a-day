# ============================================================================
# DOCKERFILE - Five a Day Django Application
# ============================================================================
# Multi-stage build: builder installs dependencies with UV, runtime is lean.

# ============================================================================
# STAGE 1: Builder - Install dependencies with UV
# ============================================================================
# Digest-pinned: the tag is mutable, so a rebuild could silently pick up a
# different image. Dependabot's docker ecosystem keeps this digest current.
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS builder

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
COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /usr/local/bin/uv

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
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=project.settings \
    PATH="/app/.venv/bin:$PATH" \
    # The academy runs on Madrid time. python:slim is UTC, so naive date.today()
    # / datetime.now() returned the UTC calendar date — a day behind local for
    # 1–2 h after midnight — and a cash payment recorded at 00:40 on the 1st
    # booked into the previous month (every income figure filters on
    # payment_date). USE_TZ=True still stores aware UTC in the DB; this only
    # aligns the naive local clock the ~65 date.today() call sites read.
    TZ=Europe/Madrid

# Install only runtime system deps
# git: used by the QA testing dashboard to show the last commit (branch, hash,
#      author, date) — see core/views/testing_tools._git_info.
# tzdata: so the TZ env var above resolves to a real zoneinfo (Madrid DST).
#
# `apt-get upgrade` applies Debian's security updates to the BASE image's own
# packages, and it is here because the digest pin above cannot do that job.
# Docker Hub rebuilds `python:3.14-slim` on its own schedule, so between a
# Debian security release and that rebuild the pinned digest is, by
# construction, out of date — and `Publish image & scan` fails the moment it is,
# on packages we never chose to install. That is not hypothetical: on
# 2026-09-13 the gate failed on five fixable HIGHs in gzip (CVE-2026-41992),
# libpcre2-8-0 (CVE-2026-86145, CVE-2026-89161) and libsqlite3-0
# (CVE-2026-11822, CVE-2026-11824), every one of them already fixed in Debian
# (deb13u1 → deb13u2) and none of them fixable from this repo: the pinned
# digest WAS still the newest `python:3.14-slim`, so there was no digest to bump
# to. The remedy the gate's own error message suggests did not exist.
#
# The alternative was `.github/trivyignore`, and the pip block below spells out
# why that is the wrong shape of fix: an ignore leaves the vulnerable code in
# the image and switches the control off. Upgrading leaves the control armed and
# actually removes the vulnerability.
#
# The cost is that the runtime layer is no longer bit-reproducible from the
# digest alone — two builds a week apart can carry different patch levels of the
# same packages. That is the deliberate trade: the digest pin still fixes the
# starting point (so a rebuild cannot silently land on a different Python or a
# different Debian release), and this line only ever moves packages FORWARD
# within it. `--no-install-recommends` keeps the upgrade from pulling in
# anything new, and the `rm -rf` in the same RUN keeps it to one layer.
#
# DL3005 is hadolint's blanket "do not use apt-get upgrade", aimed at exactly
# the reproducibility point above. It is ignored knowingly, not by accident —
# for a published image behind a fail-on-fixable-CVE gate, an unpatched base is
# the larger of the two problems.
# hadolint ignore=DL3008,DL3005
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
    postgresql-client \
    libpq-dev \
    git \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Remove the base image's pip. The runtime NEVER installs anything: the venv is
# resolved and built by uv in the builder stage and copied in below, and `uv` is
# on PATH for any ad-hoc need (`uv pip install …`). Nothing in entrypoint.sh, the
# Makefile, any compose file or the app shells out to pip.
#
# It is deleted because pip's VENDORED dependency manifest
# (site-packages/pip/_vendor/vendor.txt) is what the Trivy image gate reads, and
# pip 26.2.1 pins `msgpack==1.1.2` and `setuptools==70.3.0` there — two fixable
# HIGHs (GHSA-6v7p-g79w-8964, CVE-2025-47273) that failed `Publish image & scan`.
# Neither is a project dependency and NEITHER CAN BE FIXED WITH uv: uv.lock
# already carries msgpack 1.2.1 and setuptools 83.0.0, both are dev-only
# transitives (pip-audit → cachecontrol → msgpack, coverage-badge → setuptools),
# and `uv sync --no-dev` puts neither in /app/.venv at all. The versions the
# scanner sees belong to pip's own vendored tree, which uv does not manage — so
# bumping anything in pyproject.toml is a no-op for this finding. Don't try it,
# and don't reach for .github/trivyignore either: an ignore would leave the
# vulnerable vendored msgpack in the image and disable the control.
#
# The vendored msgpack code is really present, so this really removes it. The
# setuptools line is a phantom — pip/_vendor/setuptools/ does not exist in the
# image; Trivy reads the manifest, not the filesystem. Both findings go with pip.
#
# Dropping an installer from a production container is a hardening win in its
# own right: a foothold that cannot fetch packages is a worse one. Deleting in
# our own layer does not shrink the image (the base layer still holds the bytes)
# but it does remove the files from the container's filesystem, which is what
# both the scanner and an attacker in a running container see.
#
# Glob the minor version so a base-image digest bump does not silently no-op.
# The two negated checks then FAIL THE BUILD if any pip metadata survived: a
# silent miss would otherwise only surface as a red gate on the next push to
# testing or main, which is exactly the late feedback this step exists to stop.
#
# The find test uses a command substitution rather than `| grep -q` so the line
# stays free of pipes: hadolint's DL4006 fires on a piped RUN under /bin/sh, and
# the CI Hadolint step fails on warnings.
RUN rm -rf /usr/local/lib/python3.*/site-packages/pip /usr/local/lib/python3.*/site-packages/pip-*.dist-info /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.* && [ -z "$(find /usr/local/lib -path '*pip*' -name 'vendor.txt')" ] && ! command -v pip

# Cloud Run ignores HEALTHCHECK (it probes the service), but on the Compose
# testing VM this is what makes a wedged container show as unhealthy instead
# of silently serving nothing. /health/ is the shallow probe: no DB touch.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3     CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=4).status == 200 else 1)"]

# Create non-root user. UID and GID are pinned so the numeric `USER 1000:1000`
# below is unambiguous (hadolint DL3066 - a name-based USER can't be resolved by
# the host, which matters for Cloud Run / K8s runAsNonRoot checks).
RUN groupadd -g 1000 django && \
    useradd -m -u 1000 -g 1000 django && \
    mkdir -p /app /app/staticfiles /app/mediafiles && \
    chown -R django:django /app

WORKDIR /app

# Copy UV and the virtual environment from builder
COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /usr/local/bin/uv
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
# --chdir project is required: manage.py lives at /app/project/manage.py and the Django
# package at /app/project/project/, so `project.wsgi` only resolves with /app/project as
# the working directory. Without it Gunicorn dies with
# `ModuleNotFoundError: No module named 'project.wsgi'` — which never showed up locally
# because dev swaps in runserver (docker-compose.override.yml) and the QA VM pins its own
# 2-worker gunicorn line (docker-compose.testing.yml). Production is therefore still the
# only environment that runs THIS command — the v1.14.7 broken deploy. Note that since
# v1.27 the base docker-compose.yml no longer overrides `command` at all, so a bare
# `docker compose -f docker-compose.yml up` does exercise this line.
CMD ["gunicorn", "--chdir", "project", "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "project.wsgi:application"]
