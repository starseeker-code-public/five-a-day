# ============================================================================
# MAKEFILE - Five a Day eVolution
# ============================================================================
# Docker and Django shortcuts. Run `make` or `make help` for usage.

.PHONY: help setup build up down restart stop start rebuild dev logs \
        ps stats shell bash migrate makemigrations createsuperuser \
        collectstatic check dbshell backup restore reset-db \
        test test-cov-gate smoke \
        clean clean-all health url generate-payments generate-payments-dry \
        sync lint format pre-commit-install pc-run \
        mypy bandit audit coverage-badge check-deploy \
        celery-logs celery-restart celery-status celery-test-task \
        connect-testing

# ============================================================================
# COMPOSE FILE SELECTION — every target goes through $(COMPOSE)
# ============================================================================
# The QA VM is a TWO-FILE stack: the overlay is what mounts
# testing_postgres_data instead of the base file's dev volume. A one-file
# bring-up there starts a different, valid, WRONG stack that exits 0 and even
# reports the right version on /health/ — that is how a v1.16.0 deploy served a
# months-old database and left the real volume orphaned.
#
# So the file list is derived from DJANGO_ENV in the active .env rather than
# left to whoever types the command:
#   DJANGO_ENV=testing  → -f docker-compose.yml -f docker-compose.testing.yml
#   anything else       → bare `docker compose`, which auto-loads
#                         docker-compose.override.yml (the dev source mount)
# Override for one command with:  make <target> TESTING=1   (or FILES='-f …')
DJANGO_ENV_ACTIVE := $(shell [ -f .env ] && grep -m1 '^DJANGO_ENV=' .env | cut -d= -f2 | tr -d "\"' \r")
TESTING_FILES := -f docker-compose.yml -f docker-compose.testing.yml

ifeq ($(TESTING),1)
  FILES ?= $(TESTING_FILES)
else ifeq ($(DJANGO_ENV_ACTIVE),testing)
  FILES ?= $(TESTING_FILES)
else
  FILES ?=
endif

COMPOSE := docker compose $(FILES)

# ============================================================================
# HELP
# ============================================================================
help:
	@echo ""
	@echo "  Five a Day - Make Commands"
	@echo "  =========================="
	@echo ""
	@echo "  Setup & Build:"
	@echo "    make setup              Create empty .env"
	@echo "    make build              Build Docker images"
	@echo "    make rebuild            Full rebuild (no cache) + start"
	@echo "    make rebuild SERVICE=x  Rebuild only a specific service"
	@echo ""
	@echo "  Docker Lifecycle:"
	@echo "    make up                 Start all services (detached)"
	@echo "    make down               Stop and remove containers"
	@echo "    make restart            Restart all services"
	@echo "    make restart SERVICE=x  Restart a specific service"
	@echo "    make stop               Stop without removing"
	@echo "    make stop SERVICE=x     Stop a specific service"
	@echo "    make start              Start stopped containers"
	@echo "    make start SERVICE=x    Start a specific service"
	@echo "    make dev                Start in foreground (logs visible)"
	@echo "    make dev BUILD=1        Build + start in foreground"
	@echo ""
	@echo "  Monitoring:"
	@echo "    make logs               Tail all logs"
	@echo "    make logs SERVICE=x     Tail logs for a specific service"
	@echo "    make ps                 Show running services"
	@echo "    make stats              Show resource usage"
	@echo "    make health             Full health check (Django + DB)"
	@echo "    make url                Show access URLs"
	@echo ""
	@echo "  Django:"
	@echo "    make shell              Django shell inside container"
	@echo "    make bash               Bash shell inside container"
	@echo "    make migrate            Apply all migrations"
	@echo "    make makemigrations     Create migrations (all apps)"
	@echo "    make createsuperuser    Create Django superuser"
	@echo "    make collectstatic      Collect static files"
	@echo "    make check              Run Django system checks"
	@echo ""
	@echo "  Database:"
	@echo "    make dbshell            PostgreSQL interactive shell"
	@echo "    make backup             Dump LOCAL dev DB to backups/ (NOT production)"
	@echo "    make restore FILE=x     Restore from SQL file"
	@echo "    make reset-db           Drop and recreate DB (destructive! typed confirmation)"
	@echo ""
	@echo "  Compose files in use for every target above:"
	@echo "    $(if $(FILES),docker compose $(FILES),docker compose  (base + docker-compose.override.yml))"
	@echo "    DJANGO_ENV in .env: $(if $(DJANGO_ENV_ACTIVE),$(DJANGO_ENV_ACTIVE),<unset>)   (override with TESTING=1)"
	@echo ""
	@echo "  Testing:"
	@echo "    make test               Run all tests (Docker + coverage)"
	@echo "    make test unit          Run only unit tests"
	@echo "    make test integration   Run only integration tests"
	@echo "    make test coverage      All tests + HTML coverage report"
	@echo "    make test K=<keyword>   Filter by keyword  (e.g. K=payment)"
	@echo "    make test ARGS='...'    Pass raw pytest flags through"
	@echo "    make smoke              End-to-end smoke test vs the running stack (local dev only)"
	@echo ""
	@echo "  Payments:"
	@echo "    make generate-payments          Generate current month"
	@echo "    make generate-payments-dry      Preview without creating"
	@echo ""
	@echo "  Celery (async tasks):"
	@echo "    make celery-logs        Tail Celery worker + beat logs"
	@echo "    make celery-restart     Restart worker + beat containers"
	@echo "    make celery-status      Show Celery worker status"
	@echo "    make celery-test-task   Send a debug task to verify Celery works"
	@echo ""
	@echo "  Developer Tooling:"
	@echo "    make sync               Install all deps (including dev) via uv"
	@echo "    make lint               Check code with Ruff (read-only)"
	@echo "    make lint FIX=1         Lint and auto-fix issues"
	@echo "    make format             Format code with Ruff"
	@echo "    make format DRY=1       Check formatting without applying changes"
	@echo "    make mypy               Run mypy type checker"
	@echo "    make bandit             Run bandit security linter"
	@echo "    make audit              Audit dependencies for vulnerabilities"
	@echo "    make coverage-badge     Generate coverage.svg badge"
	@echo "    make pre-commit-install Install pre-commit hooks"
	@echo "    make pc-run             Run pre-commit on all files"
	@echo ""
	@echo "  Cleanup:"
	@echo "    make clean              Remove stopped containers + prune"
	@echo "    make clean-all          LOCAL DEV ONLY: remove this project's containers,"
	@echo "                            named volumes and unused images (refused on testing/prod)"
	@echo ""
	@echo "  Remote:"
	@echo "    make connect-testing    SSH into the GCP testing VM (auto-login if needed)"
	@echo ""

# ============================================================================
# SETUP
# ============================================================================
setup:
	@if [ -f .env ]; then \
		echo ".env already exists."; \
	elif [ -f .env.development ]; then \
		cp .env.development .env; \
		echo "Copied .env.development → .env. Edit it if needed, then run 'make up'."; \
	else \
		touch .env; \
		echo "Created empty .env. See README.md (section '.env template') for the variable shape, or create .env.development/.env.testing/.env.production and rename one to .env."; \
	fi

# ============================================================================
# DOCKER COMPOSE - LIFECYCLE
# ============================================================================
build:
	$(COMPOSE) build

# -V (--renew-anon-volumes): docker-compose.override.yml mounts an anonymous
# volume over /app/.venv, and Compose reuses it on recreate — after a base bump
# the stale venv shadows the image's and django crash-loops on ModuleNotFoundError
# (this took down the testing VM on the v1.26.6 3.12→3.14 bump). -V only renews
# anonymous volumes; named volumes (postgres_data, redis_data) are untouched.
up:
	$(COMPOSE) up -d -V --remove-orphans
	@echo "Started: http://localhost:8000"

down:
	$(COMPOSE) down

# Rebuild with no cache. Without SERVICE rebuilds everything; with SERVICE=web
# only that service is stopped/rebuilt/started.
rebuild:
	@if [ -z "$(SERVICE)" ]; then \
		$(COMPOSE) down; \
		$(COMPOSE) build --no-cache; \
		$(COMPOSE) up -d -V; \
		echo "Rebuilt and started: http://localhost:8000"; \
	else \
		$(COMPOSE) stop $(SERVICE); \
		$(COMPOSE) build --no-cache $(SERVICE); \
		$(COMPOSE) up -d -V $(SERVICE); \
		echo "Rebuilt service: $(SERVICE)"; \
	fi

restart:
	$(COMPOSE) restart $(SERVICE)

stop:
	$(COMPOSE) stop $(SERVICE)

start:
	$(COMPOSE) start $(SERVICE)

dev:
	$(COMPOSE) up -V $(if $(BUILD),--build,) --remove-orphans

# ============================================================================
# MONITORING
# ============================================================================
logs:
	$(COMPOSE) logs -f $(SERVICE)

ps:
	$(COMPOSE) ps

stats:
	docker stats

health:
	@echo "=== Services ==="
	@$(COMPOSE) ps
	@echo ""
	@echo "=== Django check ==="
	@$(COMPOSE) exec web python project/manage.py check 2>/dev/null || echo "(web not running)"
	@echo ""
	@echo "=== PostgreSQL ==="
	@$(COMPOSE) exec db sh -c 'pg_isready -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"' 2>/dev/null || echo "(db not running)"
	@echo ""
	@echo "=== Health endpoint ==="
	@curl -sf http://localhost:8000/health/ 2>/dev/null || echo "(not reachable)"

url:
	@echo "App:   http://localhost:8000"
	@echo "Admin: http://localhost:8000/admin"
	@echo "Login: http://localhost:8000/login"

# ============================================================================
# DJANGO COMMANDS
# ============================================================================
shell:
	$(COMPOSE) exec web python project/manage.py shell

bash:
	$(COMPOSE) exec web bash

migrate:
	$(COMPOSE) exec web python project/manage.py migrate

makemigrations:
	$(COMPOSE) exec web python project/manage.py makemigrations students billing core comms

createsuperuser:
	$(COMPOSE) exec web python project/manage.py createsuperuser

collectstatic:
	$(COMPOSE) exec web python project/manage.py collectstatic --noinput

check:
	$(COMPOSE) exec web python project/manage.py check

# ============================================================================
# DATABASE
# ============================================================================
# Credentials are NEVER hard-coded here: they are read from the db container's
# own environment ($POSTGRES_USER / $POSTGRES_DB, which Compose sets from .env
# with a change_this_password-style default). The old literals
# `-U fiveaday_user -d fiveaday_db` matched the compose DEFAULTS, not what any
# real environment uses — so on the QA VM, or on any dev box that set its own
# POSTGRES_USER, every one of these targets failed with "role does not exist"
# (or, worse, connected to a different database than the app).
dbshell:
	$(COMPOSE) exec db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

# LOCAL DEVELOPMENT ONLY. Dumps the `db` container on THIS machine and never
# contacts Cloud SQL. The production equivalent is the managed Cloud SQL backup
# set (see "Backups and Recovery" in DEPLOYMENT.md) plus the gitignored
# scripts/export_prod_db.sh — which must never be recreated, because the dump
# contains personal data for real students, including minors.
backup:
	@if [ "$(DJANGO_ENV_ACTIVE)" = "production" ]; then \
		echo "REFUSING: .env says DJANGO_ENV=production. 'make backup' is a local dev pg_dump,"; \
		echo "not a production backup path. Use the Cloud SQL backups (DEPLOYMENT.md)."; \
		exit 1; \
	fi
	@mkdir -p backups
	@if [ -n "$(DJANGO_ENV_ACTIVE)" ] && [ "$(DJANGO_ENV_ACTIVE)" != "development" ]; then \
		echo "NOTE: .env says DJANGO_ENV=$(DJANGO_ENV_ACTIVE) — dumping THAT stack's db container."; \
	fi
	$(COMPOSE) exec -T db sh -c 'pg_dump -U "$$POSTGRES_USER" "$$POSTGRES_DB"' > backups/backup_$$(date +%Y%m%d_%H%M%S).sql
	@echo "Local DB dumped to backups/ (this is NOT a production backup)"

restore:
	@if [ -z "$(FILE)" ]; then \
		echo "Usage: make restore FILE=backups/backup.sql"; \
		exit 1; \
	fi
	$(COMPOSE) exec -T db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"' < $(FILE)
	@echo "Restored from $(FILE)"

# DESTRUCTIVE. `down -v` removes the named volume of WHICHEVER stack the
# selected compose files describe — which is why this target must never run
# one-file on the QA VM: it would drop the dev `postgres_data` volume, then
# bring the stack back up on it and leave the real testing_postgres_data
# orphaned (a valid, wrong stack that answers /health/ with the right version).
# $(COMPOSE) carries the testing overlay automatically when .env says
# DJANGO_ENV=testing; production is refused outright.
reset-db:
	@if [ "$(DJANGO_ENV_ACTIVE)" = "production" ]; then \
		echo "REFUSING: .env says DJANGO_ENV=production."; \
		exit 1; \
	fi
	@echo "WARNING: this DESTROYS ALL DATA in the database of this stack."
	@echo "  compose files : $(if $(FILES),$(FILES),(base + docker-compose.override.yml))"
	@echo "  DJANGO_ENV    : $(if $(DJANGO_ENV_ACTIVE),$(DJANGO_ENV_ACTIVE),<unset>)"
	@if [ "$(DJANGO_ENV_ACTIVE)" = "testing" ]; then \
		echo ""; \
		echo "  This is the QA/testing stack — the data QA has been entering."; \
	fi
	@echo ""
	@read -p "Type 'reset $(if $(DJANGO_ENV_ACTIVE),$(DJANGO_ENV_ACTIVE),development)' to confirm: " confirm; \
	if [ "$$confirm" = "reset $(if $(DJANGO_ENV_ACTIVE),$(DJANGO_ENV_ACTIVE),development)" ]; then \
		$(COMPOSE) down -v; \
		$(COMPOSE) up -d -V; \
		sleep 15; \
		echo "Database recreated."; \
		$(COMPOSE) ps; \
	else \
		echo "Cancelled."; \
	fi

# ============================================================================
# TESTING
# ============================================================================
# All tests run inside Docker against PostgreSQL (same engine as production).
#
# Usage:
#   make test                 all tests with coverage
#   make test unit            tests/unit/ only
#   make test integration     tests/integration/ only
#   make test coverage        all tests + HTML report (htmlcov/)
#   make test K=payment       filter by keyword
#   make test ARGS='--lf'     pass any raw pytest flag through

ifeq ($(firstword $(MAKECMDGOALS)),test)
  _SUITE := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  ifneq ($(_SUITE),)
    $(eval $(_SUITE):;@:)
  endif
endif

test:
	$(COMPOSE) exec web uv sync --frozen --no-install-project --quiet
	@SUITE="$(_SUITE)"; \
	TEST_PATH="project/tests/"; \
	EXTRA=""; \
	case "$$SUITE" in \
	  unit)        TEST_PATH="project/tests/unit/" ;; \
	  integration) TEST_PATH="project/tests/integration/" ;; \
	  coverage)    EXTRA="--cov-report=html" ;; \
	esac; \
	[ -n "$(K)" ] && EXTRA="$$EXTRA -k $(K)"; \
	$(COMPOSE) exec \
	  -e DJANGO_SETTINGS_MODULE=project.settings_test \
	  -e TEST_DB_HOST=db \
	  web python -m pytest $$TEST_PATH -v --tb=short -n auto \
	  --cov=core --cov=students --cov=billing --cov=comms \
	  --cov-report=term-missing $$EXTRA $(ARGS)

# Pre-commit coverage gate: fails if coverage drops below 75%.
# Invoked by the pytest-coverage pre-commit hook; safe to run manually.
test-cov-gate:
	@$(COMPOSE) exec web uv sync --frozen --no-install-project --quiet
	@$(COMPOSE) exec -e DJANGO_SETTINGS_MODULE=project.settings_test -e TEST_DB_HOST=db web python -m pytest project/tests/ -q --tb=line -n auto --cov=core --cov=students --cov=billing --cov=comms --cov-fail-under=75

# ============================================================================
# SMOKE TEST (end-to-end, against a RUNNING stack — not pytest)
# ============================================================================
# Drives the real HTTP paths (/students/create/ → enrollment + payments, then
# /payments/create/) with django.test.Client against the LIVE dev database, so
# it catches what the test suite cannot: a broken URL conf, missing
# EnrollmentType reference data, a mis-scheduled first payment.
#
# LOCAL DEV ONLY, for one hard reason: `.dockerignore` excludes scripts/, so
# the file does not exist in any BUILT image. It is reachable inside the
# container solely through the docker-compose.override.yml source mount — i.e.
# on a dev box, never on the QA VM or Cloud Run.
#
# PYTHONPATH=/app is required: manage.py lives at /app/project/manage.py, so
# sys.path[0] is /app/project and the repo-root `scripts` package is otherwise
# not importable.
#
# It WRITES rows (a Smoke Group, parent SMOKE1234A, "Alumno Smoke" + their
# matrícula and mensualidad). Re-running is safe; the students accumulate.
smoke:
	$(COMPOSE) exec -e PYTHONPATH=/app web \
	  python project/manage.py shell -c "from scripts.docker_smoke_test import run; run()"

# ============================================================================
# PAYMENTS
# ============================================================================
generate-payments:
	$(COMPOSE) exec web python project/manage.py generate_payments

generate-payments-dry:
	$(COMPOSE) exec web python project/manage.py generate_payments --dry-run

# ============================================================================
# CLEANUP
# ============================================================================
clean:
	$(COMPOSE) down
	docker system prune -f

# DESTRUCTIVE, and guarded three ways.
#
# This target used to end in `docker system prune -af --volumes` — a
# DAEMON-WIDE volume wipe, the exact operation the nightly deploy workflow
# gates against and that CLAUDE.md says must never run on the testing VM,
# where `docker volume prune` once orphaned the QA database. It also reached
# far outside this project: every other container, image and volume on the
# machine went with it.
#
#   1. it refuses outright on a testing/production .env, and refuses when a
#      *_testing_postgres_data volume exists on this host at all (that volume
#      is the QA database — its presence means this is not a throwaway box);
#   2. the confirmation is a typed phrase, not "yes";
#   3. `--volumes` is GONE from the prune. `$(COMPOSE) down -v` already removes
#      this project's named volumes, which is the documented intent
#      ("everything including volumes"); the daemon-wide flag only ever added
#      collateral damage. Images/build cache are still reclaimed with -af.
clean-all:
	@if [ "$(DJANGO_ENV_ACTIVE)" = "testing" ] || [ "$(DJANGO_ENV_ACTIVE)" = "production" ]; then \
		echo "REFUSING: .env says DJANGO_ENV=$(DJANGO_ENV_ACTIVE). 'make clean-all' is a local-dev"; \
		echo "teardown; on the QA VM it would destroy the testing database. Use 'make down'."; \
		exit 1; \
	fi
	@if docker volume ls --format '{{.Name}}' 2>/dev/null | grep -q 'testing_postgres_data'; then \
		echo "REFUSING: a *_testing_postgres_data volume exists on this Docker host, so this"; \
		echo "looks like the QA VM (or a machine holding a copy of the QA database)."; \
		echo "That volume is the testing database and must never be pruned."; \
		docker volume ls --format '  {{.Name}}' | grep 'testing_postgres_data'; \
		exit 1; \
	fi
	@echo "WARNING: removes this project's containers AND its named volumes"
	@echo "(postgres_data, redis_data — i.e. the local dev database), then prunes"
	@echo "unused images and build cache for the whole Docker host."
	@echo ""
	@read -p "Type 'destroy local dev' to confirm: " confirm; \
	if [ "$$confirm" = "destroy local dev" ]; then \
		$(COMPOSE) down -v; \
		docker system prune -af; \
		echo "Local dev stack and unused images removed."; \
	else \
		echo "Cancelled."; \
	fi

# ============================================================================
# VERSIONING
# ============================================================================
# pyproject.toml is the SINGLE SOURCE OF TRUTH for the app version.
# `make version x.y.z` updates the three places that cannot read it themselves:
#   1. pyproject.toml  -> version = "x.y.z"   (the source)
#   2. README.md       -> badge URL
#   3. uv.lock         -> regenerated via `uv lock --quiet`
# settings.py holds NO copy: APP_VERSION reads pyproject.toml at import time, so
# it cannot lag. project/tests/unit/test_version_consistency.py fails the build
# if any of the three ever drifts from the source.
# Usage: make version x.y.z

ifeq ($(firstword $(MAKECMDGOALS)),version)
  _VERSION_ARG := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  ifneq ($(_VERSION_ARG),)
    $(eval $(_VERSION_ARG):;@:)
  endif
endif

version:
	@CURRENT=$$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'); \
	BADGE=$$(grep -oE 'version-v[0-9]+(\.[0-9]+)*-brightgreen' README.md | head -1 | sed -E 's/version-v(.*)-brightgreen/\1/'); \
	NEW="$(_VERSION_ARG)"; \
	if [ -z "$$NEW" ]; then \
		echo "Usage: make version x.y.z"; \
		echo ""; \
		echo "Current version:"; \
		echo "  pyproject.toml:  $$CURRENT"; \
		echo "  README.md badge: $$BADGE"; \
		if [ -n "$$BADGE" ] && [ "$$CURRENT" != "$$BADGE" ]; then \
			echo ""; \
			echo "  WARNING: pyproject and README badge are out of sync."; \
		fi; \
		exit 1; \
	fi; \
	read -p "Version $$CURRENT will become the new version $$NEW, are you sure? [y/N] " confirm; \
	if [ "$$confirm" = "y" ] || [ "$$confirm" = "yes" ]; then \
		sed -i 's/^version = ".*"/version = "'"$$NEW"'"/' pyproject.toml; \
		sed -i -E 's|version-v[0-9]+(\.[0-9]+)*-brightgreen|version-v'"$$NEW"'-brightgreen|' README.md; \
		uv lock --quiet; \
		echo "Version updated to $$NEW in:"; \
		echo "  - pyproject.toml"; \
		echo "  - README.md (badge URL)"; \
		echo "  - uv.lock (regenerated via 'uv lock')"; \
		echo "  (settings.py derives APP_VERSION from pyproject - nothing to update)"; \
		echo ""; \
		echo "NOTE: the Recent Versions table, Version History details block, and per-app"; \
		echo "      READMEs were NOT changed automatically - run the 'update-readme' skill"; \
		echo "      after staging your work to refresh them."; \
	else \
		echo "Cancelled."; \
	fi

# ============================================================================
# CELERY (async tasks + scheduled jobs)
# ============================================================================
celery-logs:
	$(COMPOSE) logs -f celery_worker celery_beat

celery-restart:
	$(COMPOSE) restart celery_worker celery_beat

celery-status:
	$(COMPOSE) exec -w /app/project celery_worker celery -A project.celery inspect active

celery-test-task:
	$(COMPOSE) exec web python project/manage.py shell -c "from project.celery import debug_task; debug_task.delay(); print('Task queued - check celery-logs')"

# ============================================================================
# DEVELOPER TOOLING (UV, Ruff, pre-commit)
# ============================================================================
sync:
	uv sync --no-install-project

lint:
	uv run --no-project ruff check $(if $(FIX),--fix,) project/

format:
	uv run --no-project ruff format $(if $(DRY),--check,) project/

mypy:
	uv run mypy project/

bandit:
	PYTHONUTF8=1 uv run bandit -r project/ -c pyproject.toml

audit:
	uv run pip-audit

coverage-badge:
	@echo "Copying .coverage from Docker container..."
	$(COMPOSE) cp web:/app/.coverage .coverage
	uv run coverage-badge -o coverage.svg -f
	@rm -f .coverage
	@echo "coverage.svg updated - commit it to the repo"

pre-commit-install:
	uv run --no-project pre-commit install

pc-run:
	@if uv run --no-project pre-commit run --all-files; then \
		read -p "Pre-commit passed. Is this a new version? [y/N] " answer; \
		if [ "$$answer" = "y" ] || [ "$$answer" = "yes" ]; then \
			CURRENT=$$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'); \
			MAJOR=$$(echo $$CURRENT | cut -d. -f1); \
			MINOR=$$(echo $$CURRENT | cut -d. -f2); \
			PATCH=$$(echo $$CURRENT | cut -d. -f3); \
			NEW="$$MAJOR.$$MINOR.$$((PATCH + 1))"; \
			sed -i 's/^version = ".*"/version = "'"$$NEW"'"/' pyproject.toml; \
			sed -i -E 's|version-v[0-9]+(\.[0-9]+)*-brightgreen|version-v'"$$NEW"'-brightgreen|' README.md; \
			uv lock --quiet; \
			echo "Updated version $$CURRENT with new version $$NEW (pyproject.toml, README badge, uv.lock; settings.py derives)"; \
			echo "Reminder: Recent Versions + Version History in README were NOT touched - run '/update-readme' skill to refresh them."; \
		fi; \
		if [ -n "$$(git status --porcelain uv.lock 2>/dev/null)" ]; then \
			git add uv.lock; \
			echo "Staged updated uv.lock"; \
		fi; \
	else \
		echo "❌ pre-commit FAILED — fix the reported issues before committing."; \
		exit 1; \
	fi

# ============================================================================
# PRODUCTION
# ============================================================================
check-deploy:
	$(COMPOSE) exec web python project/manage.py check --deploy

# ============================================================================
# REMOTE (gcloud)
# ============================================================================
# SSH into the testing VM. Triggers `gcloud auth login` only if no account is
# currently active; if you're already logged in, it skips straight to SSH.
connect-testing:
	@ACTIVE=$$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null); \
	if [ -z "$$ACTIVE" ]; then \
		echo "No active gcloud account. Launching login..."; \
		gcloud auth login || exit 1; \
	else \
		echo "Using gcloud account: $$ACTIVE"; \
	fi; \
	gcloud compute ssh --zone "us-east1-c" "fiveaday-testing" --project "five-a-day-evolution"
