# ============================================================================
# MAKEFILE - Five a Day eVolution
# ============================================================================
# Docker and Django shortcuts. Run `make` or `make help` for usage.

.PHONY: help setup build up down restart stop start rebuild dev logs \
        ps stats shell bash migrate makemigrations createsuperuser \
        collectstatic check dbshell backup restore reset-db \
        test test-cov-gate \
        clean clean-all health url generate-payments generate-payments-dry \
        sync lint format pre-commit-install pc-run \
        mypy bandit audit coverage-badge check-deploy \
        celery-logs celery-restart celery-status celery-test-task \
        connect-testing

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
	@echo "    make backup             Dump DB to backups/"
	@echo "    make restore FILE=x     Restore from SQL file"
	@echo "    make reset-db           Drop and recreate DB (destructive!)"
	@echo ""
	@echo "  Testing:"
	@echo "    make test               Run all tests (Docker + coverage)"
	@echo "    make test unit          Run only unit tests"
	@echo "    make test integration   Run only integration tests"
	@echo "    make test coverage      All tests + HTML coverage report"
	@echo "    make test K=<keyword>   Filter by keyword  (e.g. K=payment)"
	@echo "    make test ARGS='...'    Pass raw pytest flags through"
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
	@echo "    make clean-all          Remove everything including volumes"
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
	docker compose build

up:
	docker compose up -d --remove-orphans
	@echo "Started: http://localhost:8000"

down:
	docker compose down

# Rebuild with no cache. Without SERVICE rebuilds everything; with SERVICE=web
# only that service is stopped/rebuilt/started.
rebuild:
	@if [ -z "$(SERVICE)" ]; then \
		docker compose down; \
		docker compose build --no-cache; \
		docker compose up -d; \
		echo "Rebuilt and started: http://localhost:8000"; \
	else \
		docker compose stop $(SERVICE); \
		docker compose build --no-cache $(SERVICE); \
		docker compose up -d $(SERVICE); \
		echo "Rebuilt service: $(SERVICE)"; \
	fi

restart:
	docker compose restart $(SERVICE)

stop:
	docker compose stop $(SERVICE)

start:
	docker compose start $(SERVICE)

dev:
	docker compose up $(if $(BUILD),--build,) --remove-orphans

# ============================================================================
# MONITORING
# ============================================================================
logs:
	docker compose logs -f $(SERVICE)

ps:
	docker compose ps

stats:
	docker stats

health:
	@echo "=== Services ==="
	@docker compose ps
	@echo ""
	@echo "=== Django check ==="
	@docker compose exec web python project/manage.py check 2>/dev/null || echo "(web not running)"
	@echo ""
	@echo "=== PostgreSQL ==="
	@docker compose exec db pg_isready -U fiveaday_user 2>/dev/null || echo "(db not running)"
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
	docker compose exec web python project/manage.py shell

bash:
	docker compose exec web bash

migrate:
	docker compose exec web python project/manage.py migrate

makemigrations:
	docker compose exec web python project/manage.py makemigrations students billing core comms

createsuperuser:
	docker compose exec web python project/manage.py createsuperuser

collectstatic:
	docker compose exec web python project/manage.py collectstatic --noinput

check:
	docker compose exec web python project/manage.py check

# ============================================================================
# DATABASE
# ============================================================================
dbshell:
	docker compose exec db psql -U fiveaday_user -d fiveaday_db

backup:
	@mkdir -p backups
	docker compose exec db pg_dump -U fiveaday_user fiveaday_db > backups/backup_$$(date +%Y%m%d_%H%M%S).sql
	@echo "Backup saved to backups/"

restore:
	@if [ -z "$(FILE)" ]; then \
		echo "Usage: make restore FILE=backups/backup.sql"; \
		exit 1; \
	fi
	docker compose exec -T db psql -U fiveaday_user -d fiveaday_db < $(FILE)
	@echo "Restored from $(FILE)"

reset-db:
	@echo "WARNING: This will destroy ALL data in the database."
	@read -p "Type 'yes' to confirm: " confirm; \
	if [ "$$confirm" = "yes" ]; then \
		docker compose down -v; \
		docker compose up -d; \
		sleep 15; \
		echo "Database recreated."; \
		docker compose ps; \
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
	docker compose exec web uv sync --frozen --no-install-project --quiet
	@SUITE="$(_SUITE)"; \
	TEST_PATH="project/tests/"; \
	EXTRA=""; \
	case "$$SUITE" in \
	  unit)        TEST_PATH="project/tests/unit/" ;; \
	  integration) TEST_PATH="project/tests/integration/" ;; \
	  coverage)    EXTRA="--cov-report=html" ;; \
	esac; \
	[ -n "$(K)" ] && EXTRA="$$EXTRA -k $(K)"; \
	docker compose exec \
	  -e DJANGO_SETTINGS_MODULE=project.settings_test \
	  -e TEST_DB_HOST=db \
	  web python -m pytest $$TEST_PATH -v --tb=short -n auto \
	  --cov=core --cov=students --cov=billing --cov=comms \
	  --cov-report=term-missing $$EXTRA $(ARGS)

# Pre-commit coverage gate: fails if coverage drops below 75%.
# Invoked by the pytest-coverage pre-commit hook; safe to run manually.
test-cov-gate:
	@docker compose exec web uv sync --frozen --no-install-project --quiet
	@docker compose exec -e DJANGO_SETTINGS_MODULE=project.settings_test -e TEST_DB_HOST=db web python -m pytest project/tests/ -q --tb=line -n auto --cov=core --cov=students --cov=billing --cov=comms --cov-fail-under=75

# ============================================================================
# PAYMENTS
# ============================================================================
generate-payments:
	docker compose exec web python project/manage.py generate_payments

generate-payments-dry:
	docker compose exec web python project/manage.py generate_payments --dry-run

# ============================================================================
# CLEANUP
# ============================================================================
clean:
	docker compose down
	docker system prune -f

clean-all:
	@echo "WARNING: This will remove ALL containers, images, and volumes."
	@read -p "Type 'yes' to confirm: " confirm; \
	if [ "$$confirm" = "yes" ]; then \
		docker compose down -v; \
		docker system prune -af --volumes; \
		echo "Everything removed."; \
	else \
		echo "Cancelled."; \
	fi

# ============================================================================
# VERSIONING
# ============================================================================
# App version is defined in four places and `make version x.y.z` updates them all:
#   1. pyproject.toml  -> version = "x.y.z"
#   2. settings.py     -> APP_VERSION fallback = "x.y.z"
#   3. README.md       -> badge URL
#   4. uv.lock         -> regenerated via `uv lock --quiet`
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
		sed -i 's/APP_VERSION = os.getenv("APP_VERSION", ".*")/APP_VERSION = os.getenv("APP_VERSION", "'"$$NEW"'")/' project/project/settings.py; \
		sed -i -E 's|version-v[0-9]+(\.[0-9]+)*-brightgreen|version-v'"$$NEW"'-brightgreen|' README.md; \
		uv lock --quiet; \
		echo "Version updated to $$NEW in:"; \
		echo "  - pyproject.toml"; \
		echo "  - project/project/settings.py"; \
		echo "  - README.md (badge URL)"; \
		echo "  - uv.lock (regenerated via 'uv lock')"; \
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
	docker compose logs -f celery_worker celery_beat

celery-restart:
	docker compose restart celery_worker celery_beat

celery-status:
	docker compose exec -w /app/project celery_worker celery -A project.celery inspect active

celery-test-task:
	docker compose exec web python project/manage.py shell -c "from project.celery import debug_task; debug_task.delay(); print('Task queued - check celery-logs')"

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
	docker compose cp web:/app/.coverage .coverage
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
			sed -i 's/APP_VERSION = os.getenv("APP_VERSION", ".*")/APP_VERSION = os.getenv("APP_VERSION", "'"$$NEW"'")/' project/project/settings.py; \
			sed -i -E 's|version-v[0-9]+(\.[0-9]+)*-brightgreen|version-v'"$$NEW"'-brightgreen|' README.md; \
			uv lock --quiet; \
			echo "Updated version $$CURRENT with new version $$NEW (pyproject.toml, settings.py, README badge, uv.lock)"; \
			echo "Reminder: Recent Versions + Version History in README were NOT touched - run '/update-readme' skill to refresh them."; \
		fi; \
	fi
	@if [ -n "$$(git status --porcelain uv.lock 2>/dev/null)" ]; then \
		git add uv.lock; \
		echo "Staged updated uv.lock"; \
	fi

# ============================================================================
# PRODUCTION
# ============================================================================
check-deploy:
	docker compose exec web python project/manage.py check --deploy

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
