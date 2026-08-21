# CLAUDE.md — Project Context for AI-Assisted Development

## What is this project?

Five a Day eVolution is a Django student management system for a small English academy in Albacete, Spain. It manages students, parents, enrollments, payments, class scheduling, and automated email communications. The system is designed for 3-10 admin users managing up to 2,000 students.

## Architecture

### 4 Django apps

- **students** — People models (Student, Parent, Teacher, Group, StudentParent). No views of its own — views live in `core/views/`.
- **billing** — Financial models (SiteConfiguration, EnrollmentType, Enrollment, Payment). Contains the service layer (EnrollmentService, PaymentService, PricingService) where business logic lives.
- **comms** — No models. Email service (EmailService), email convenience functions, Celery tasks, management commands for sending emails.
- **core** — Cross-cutting models (TodoItem, HistoryLog, FunFridayAttendance, FunFridayScheduledSend, ScheduleSlot). Owns ALL views (split into 12 modules in `core/views/`), ALL templates, middleware, and URL routing for dashboard/auth/schedule.

### Key design decisions

- **Views stay in core** — while models are split across apps, all views remain in `core/views/` for simplicity. Each app's `urls.py` imports views from `core.views`.
- **Service layer in billing** — business logic (enrollment creation, payment calculation, pricing) is extracted from views/forms into `billing/services/`. Forms delegate to services.
- **SiteConfiguration is the single source of truth for pricing** — `billing/constants.py` has seed values only used when creating the initial config row.
- **Two-mode auth** — in **development** the login view compares against `LOGIN_USERNAME`/`LOGIN_PASSWORD` env vars (and get-or-creates a matching Django superuser so `/admin/` keeps working). In **testing/production** the same view authenticates against `auth.User` via `django.contrib.auth.authenticate` — Teachers log in with their email + hashed password through the `Teacher.user` OneToOneField. Google OAuth always backs into the same Django `ModelBackend` (get-or-creates a superuser + links to a Teacher by email). `SimpleAuthMiddleware` adds a second authorization layer: when the session belongs to a non-admin Teacher, only URLs in `NON_ADMIN_ALLOWED_URL_NAMES` are reachable.
- **Tailwind CSS via CDN** — no build tools. Custom violet palette defined in `base.html`'s Tailwind config block.
- **All JS extracted to static files** — 13 modules in `core/static/js/`. Django template variables passed via `data-*` attributes or small inline config scripts.
- **PostgreSQL everywhere** — same database engine in development, testing, and production. Never use SQLite for anything other than quick local fallback (`TEST_DB_ENGINE=sqlite`).

### Dependency flow

```text
students ← billing (FK refs)
students ← core (FunFridayAttendance, ScheduleSlot FK refs)
students ← comms (email recipient resolution)
billing ← comms (tax certificate PDF generation)
billing ← core/views (dashboard stats, payment views)
comms ← core/views (student creation triggers welcome email)
```

## How to run

```bash
uv sync --no-install-project   # Install all dependencies
make up                        # Start Docker (PostgreSQL + Django)
make test                      # Run tests (PostgreSQL)
```

### Testing — IMPORTANT

**Always use `make test`** (runs inside Docker against PostgreSQL — same database as production). Never use SQLite for testing unless explicitly asked. The `make test` command requires Docker containers to be running (`make up` first).

**IMPORTANT for AI agents:** Do NOT use `TEST_DB_ENGINE=sqlite` or run pytest directly with `python -m pytest`. Always use `make test` which runs against the Docker PostgreSQL container. If `make test` fails with connection errors, run `make up` first.

### Developer tooling

```bash
make lint              # Ruff linter
make format            # Ruff formatter
make pc-run            # Run all pre-commit hooks (dry run + auto version bump)
make test              # Run the full suite (PostgreSQL via Docker, parallel, with coverage)
```

- **UV** for dependency management (see [docs/UV.md](docs/UV.md))
- **Ruff** for linting and formatting (`pyproject.toml [tool.ruff]`)
- **pre-commit** hooks run Ruff on every commit (`.pre-commit-config.yaml`)
- **pytest-cov** for coverage reports (`make test-coverage`)

## Important files

| File | What it does |
| ---- | ------------ |
| `project/settings.py` | Main settings — DB config, email, Celery, middleware |
| `project/settings_test.py` | Test overrides — PostgreSQL default with SQLite fallback |
| `project/urls.py` | Root URL conf — includes students, billing, comms, core |
| `core/views/__init__.py` | Re-exports all views for URL routing compatibility |
| `core/templates/base.html` | Main layout — Tailwind CDN config, sidebar, header, support modal |
| `core/middleware.py` | SimpleAuthMiddleware — session-based auth |
| `core/context_processors.py` | Injects notifications, todos, history count into all templates |
| `billing/models.py` | SiteConfiguration (singleton), Enrollment, Payment + academic year helpers |
| `billing/services/enrollment_service.py` | Enrollment creation with all discount logic |
| `billing/services/payment_service.py` | Payment calculation (monthly, quarterly, discounts) |
| `comms/services/email_service.py` | EmailService class + global singleton |
| `conftest.py` | Pytest fixtures — all models + authenticated_client |

## Conventions

- **Language**: Code in English, UI/templates in Spanish. Comments are mixed.
- **Models**: Every model has explicit `db_table`. All use `created_at`/`updated_at` timestamps. BigAutoField PKs.
- **Views**: Mix of CBVs (student CRUD) and FBVs (everything else). AJAX endpoints return `JsonResponse` with `{"success": bool, ...}`.
- **Forms**: Django ModelForms for data entry. EnrollmentForm delegates to EnrollmentService.
- **Templates**: Extend `base.html`. All names in English. Blocks: `title`, `sidebar_class`, `sidebar_hover`, `page_title`, `page_subtitle`, `header_actions`, `extra_css`, `content`, `modals`, `extra_js`.
- **JS**: External files in `core/static/js/`. Django data passed via `data-*` attrs on body or small `<script>window.CONFIG = {...}</script>` blocks.
- **Testing**: pytest with pytest-django. Tests in `project/tests/`. Fixtures in `conftest.py`. All view tests use `authenticated_client` fixture. Tests run against PostgreSQL by default.

## Common tasks

### Add a new email template

1. Create `core/templates/emails/your_template.html` extending `emails/base_email.html`
2. Add a convenience function in `comms/services/email_functions.py`
3. Add a Celery task in `comms/tasks.py` if it should be async
4. Add a form view in `core/views/app_forms.py` if it needs a manual send UI
5. Add it to `comms/management/commands/test_all_emails.py` for testing

### Add a new model

1. Decide which app it belongs to (students=people, billing=money, core=cross-cutting)
2. Add the model with explicit `db_table`
3. Run `python manage.py makemigrations <app> && python manage.py migrate`
4. Add admin registration in the app's `admin.py`
5. Add fixtures in `conftest.py` and tests in `tests/test_models.py`

### Add a new view

1. Create the view function/class in the appropriate `core/views/` module
2. Add it to `core/views/__init__.py` re-exports
3. Add the URL pattern to the relevant app's `urls.py`
4. Add a test in `tests/test_views.py`

### Modify pricing logic

All pricing flows through `billing/services/`. The single source of truth is `SiteConfiguration` (DB). Never hardcode prices in views or templates — read from config.

## Gotchas

- **AJAX CSRF token comes from the hidden input, NOT the cookie** — `CSRF_COOKIE_HTTPONLY` is `True` whenever `DEBUG=False` (testing/production), so JS cannot read the `csrftoken` cookie via `document.cookie`. Every AJAX helper must read the token from the hidden `{% csrf_token %}` input (`document.querySelector('[name=csrfmiddlewaretoken]')?.value`, rendered globally in `base.html`), with the cookie only as a fallback. `base.js` exposes `window.CSRF_TOKEN` built this way. A cookie-only reader works in dev (`DEBUG=True`, non-HttpOnly cookie) but silently 403s every POST in the testing/prod stack — this bug hid an entire class of "broken" features (payments, Fun Friday, todos). When adding a new AJAX POST, reuse the input-first pattern.
- **QA `/testing/` dashboard is gated on a logged-in ADMIN Teacher** — `qa_access_required` (`core/decorators.py`) + `show_testing_tools` (`core/context_processors.py`) grant access only to an authenticated Teacher with `admin=True` when `IS_TESTING_ENV` (non-admin teachers get a 404 and never see the icon — they must not reach the dev tools). There is no `QA_TESTING_USERNAME` / `manitas` user anymore. QA URLs are admin-only, so they are NOT in `NON_ADMIN_ALLOWED_URL_NAMES` (admins bypass that whitelist anyway).
- **Enrolling a student schedules the whole academic year of payments** — `StudentCreateView` (and waiting-list assignment) create the enrollment-fee payment AND call `PaymentService.schedule_academic_year_payments(enrollment, parent)`, which generates the pending monthly (Sep–Jun) or quarterly (Oct/Jan/Apr) fees due at period end. It's idempotent (matches on payment_type + due-date month/year), so the periodic `generate_payments` cron never double-creates. Don't add a second scheduling call.
- **Light + dark theme via `html.dark` overrides, NOT `dark:` variants** — the app uses the violet `primary` palette (primary-500 `#8b5cf6`) and hard-coded Tailwind utility classes. The **dark** theme lives entirely in `core/static/css/theme.css` as `html.dark .<utility>` overrides (violet-tinted dark surfaces via `--fad-*` vars, dark status badges, pagination `.pg-*`, schedule/apps/testing cards). Inline `style="background:#fff"` cards are caught with attribute selectors (`html.dark [style*="background:#fff"] { … !important }`). `darkMode: 'class'` is set; `theme.js` toggles `html.dark` (header + login toggle). When you add a new surface/text utility (or a `primary-*` shade, or a custom component class) to a template, add its `html.dark` override to `theme.css`. Schedule group-name colours are computed per-theme in `schedule.js` `cellText()` and re-render on toggle via a MutationObserver.
- **Theme default is TIME-BASED and session-scoped** — when the user hasn't explicitly toggled, the pre-paint inline script in `base.html`'s `<head>` picks **light 10:00–16:59, dark otherwise**. An explicit choice persists in `localStorage` (`theme` key) ONLY during the session: the login page (`login.html`) clears `localStorage.theme` on load (reaching `/login/` means the session ended) and re-applies the time-based default. Sessions expire after **6h of inactivity** (`SESSION_COOKIE_AGE=21600` + `SESSION_SAVE_EVERY_REQUEST=True`). `login.html` is standalone (its own `<style>`) and carries its own dark rules + toggle. A few spots hard-code the violet hex (`home.html` gradient, `403/405.html`).
- **Email templates have their own dark mode in `base_email.html`** — all 18 templates under `core/templates/emails/` extend `base_email.html` and share a common violet style matching `welcome_student.html` (violet headings `#6d28d9`, rounded info cards, coloured callouts, `#ddd6fe` table dividers). Email clients strip `<link>` stylesheets and don't support classes reliably, so content templates use **inline `style=""`** and dark mode lives as an inline `@media (prefers-color-scheme: dark)` `<style>` block in `base_email.html`'s `<head>` that overrides those exact inline hex values with **attribute selectors** (`[style*="#6d28d9"] { color: … !important }`) — the same technique `theme.css` uses for the webapp. When you add a new email template, reuse the shared palette hexes so the existing dark overrides catch it; if you introduce a new colour, add its dark override to the `base_email.html` `<style>` block. The signature/legal footer content in `base_email.html` must NOT change — only its dark rendering is themed.
- **`NoHtmlCacheMiddleware` marks dynamic HTML `no-cache`** — content-hashed static assets are served `immutable` (10y), so if a dynamic HTML page is browser-cached it pins OLD asset hashes and the theme goes stale after a deploy. `core.middleware.NoHtmlCacheMiddleware` (in `MIDDLEWARE`, right after WhiteNoise) sets `Cache-Control: no-cache, no-store, must-revalidate` on every `text/html` response so navigation always revalidates and loads current hashes. The PWA service worker is **cache-first** for hashed static (optimal); don't switch it to network-first.
- **`git` is required in the Docker image** — the QA testing dashboard's "last commit" card runs `git log` (`_git_info` in `core/views/testing_tools.py`, with `-c safe.directory=*`). `git` is installed in the `Dockerfile` runtime stage; without it the card is empty.
- **Payments allow no parent for adult students** — adults have no parent/guardian, which is valid. `create_payment` (`core/views/payments.py`) requires a parent only for non-adult students; for `student.is_adult` the payment is created with `parent=None`. The create-payment JS mirrors this (`selectedStudent.noParent` skips the parent requirement in the submit guard). `Payment.parent` is nullable.
- **No `active` field on Payment** — soft-delete was planned but never implemented. Don't filter by `active=True` on Payment.
- **academic_year format** — always "YYYY-YYYY" (e.g., "2025-2026"). September starts the new year.
- **SiteConfiguration singleton** — always access via `SiteConfiguration.get_config()`, never query directly. It auto-creates with defaults if missing.
- **Celery eager mode** — without Redis, tasks run synchronously. Don't rely on task.delay() being truly async in development.
- **NEVER schedule with `apply_async(eta=...)` / `countdown`** — production runs `CELERY_TASK_ALWAYS_EAGER=True` (Cloud Run, no worker), where the ETA is silently ignored and the task executes immediately. For time-delayed work, persist a row and drain it from a periodic task — the pattern is `FunFridayScheduledSend` (core model): the Fun Friday form stores the announcement with `scheduled_for=Monday 14:30`, and `comms.tasks.send_due_fun_friday_emails_task` (Beat daily 14:30, or the `send_due_fun_friday_emails` command in production) sends due rows idempotently (`sent_at` marks them; if the slot already passed at creation, the view drains immediately).
- **Every Celery Beat task needs a management-command wrapper** — production has no Beat process; Cloud Scheduler triggers Cloud Run Jobs that run management commands. Existing wrappers: `generate_payments`, `send_birthday_emails`, `send_payment_reminders`, `send_monthly_report`, `send_due_fun_friday_emails`, `materialize_recurring_expenses` (`--daily` for the weekly/yearly pass), `cleanup_backlog_tasks` (QA-only). They call the task synchronously via `.apply().get()`. When you add a Beat schedule entry, add a wrapper command too and document it in `DEPLOYMENT.md`'s schedule table.
- **`#webcrumbs` removed** — the old CSS scoping wrapper is gone. If you see references to it in old code, delete them.
- **Template names are English** — all email templates were renamed from Spanish (e.g., `matricula_niño.html` → `enrollment_child.html`). Never create templates with Spanish names.
- **Version in four places** — `pyproject.toml`, `project/project/settings.py` (`APP_VERSION` default), the `README.md` header badge URL, and `uv.lock` (the project's own `[[package]]` entry). `make version x.y.z` (positional, with y/N confirmation) updates the first three with `sed` and regenerates `uv.lock` via `uv lock --quiet`. Running `make version` with no argument prints the pyproject and README badge values side-by-side and warns if they've drifted. `make pc-run`'s auto patch-bump does the same four updates, then the existing `git add uv.lock` block at the end of the target stages the regenerated lockfile so the next commit isn't blocked.
- **APP_VERSION in `.env`** — the local `.env` may contain a legacy `APP_VERSION=0.x.y` line. Either remove the line or update it — it silently overrides the default in `settings.py` at runtime.
- **`load_dotenv(override=False)` — Docker / Cloud Run env vars take precedence** — `settings.py` calls `load_dotenv(".env", override=False)`. Variables already set in the process (Docker Compose `environment:` blocks, Cloud Run `--set-env-vars`/`--set-secrets`) are NOT overridden by the file. This is how `docker-compose.yml` injects Docker-network values like `POSTGRES_HOST=db` and `CELERY_BROKER_URL=redis://redis:6379/0` without the `.env` having to know about Docker topology. Don't switch to `override=True` — it would let stale `.env` values silently mask Cloud Run secrets in production.
- **`pc-run` renamed** — the old `make pre-commit-run` target is now `make pc-run`. It also auto-bumps the patch version on a clean pass (y/N prompt) and auto-stages `uv.lock` if regenerated.
- **mypy CI job needs `DJANGO_DEBUG=True`** — `django-stubs` imports `project.settings` at load time for the Django plugin. Without `DJANGO_DEBUG=True` + a dummy `DJANGO_SECRET_KEY`, the production guard at the top of `settings.py` raises `ValueError: DJANGO_SECRET_KEY debe ser cambiado en producción`. The CI `mypy` step sets both, plus `PYTHONPATH=project`, as env vars. Any new static-analysis job that imports settings will need the same.
- **Test settings disable production security redirects** — `settings_test.py` explicitly sets `SECURE_SSL_REDIRECT = False`, `SECURE_HSTS_SECONDS = 0`, `SESSION_COOKIE_SECURE = False`, `CSRF_COOKIE_SECURE = False`. Don't remove them — the Django test client speaks HTTP against `testserver`, and inheriting `SECURE_SSL_REDIRECT=True` from `settings.py` (which kicks in when `DEBUG=False`) turns every test request into a 301 to `https://testserver/...`. The overrides keep the test settings self-contained regardless of how CI configures `DJANGO_DEBUG`.
- **WhiteNoise warning is filtered** — `pytest.ini` has `filterwarnings = ignore:No directory at:UserWarning` to silence the once-per-request warning from WhiteNoise middleware when `STATIC_ROOT` (`staticfiles/`) doesn't exist. That directory only exists after `collectstatic` runs (production only), so the warning is noise in tests. Don't add `collectstatic` to the test command.
- **Three env files, one `.env` active at a time** — the repo ships `.env.development`, `.env.testing`, and `.env.production`. `settings.py` only ever loads a file named exactly `.env`. To switch environments, rename one of the three to `.env` (typically `mv .env.development .env` for local dev, `mv .env.testing .env` on the testing VM). All three are gitignored via `.env*`. `.env.production` is for **local production simulation only** — real production reads its env from Cloud Run `--set-env-vars` and Secret Manager, never from a file. There is no longer an overlay system: each file is fully self-contained.
- **`TEACHER_SEED_<N>_*` env var contract** — the `seed_teachers` management command reads numbered blocks from whichever file is active as `.env` (N starts at 1, iteration stops at the first missing `FIRST_NAME`). Each block must set `FIRST_NAME`, `LAST_NAME`, `EMAIL`; `PHONE`, `ADMIN` (default false), and `PASSWORD` are optional. Omitting `PASSWORD` creates the linked user with `unusable_password` so they must activate via `/password-reset/`. Re-running is idempotent and never overwrites a password an admin later changed (only sets one when the user's password is still unusable). `entrypoint.sh` invokes the command on container start when `DJANGO_ENV` is `testing` or `production`.
- **`Teacher.user` link sync** — Teacher has a nullable `OneToOneField` to `auth.User`. Never set the auth fields by hand; use `Teacher.ensure_user(password=...)` to get-or-create the linked user, and the `post_save` signal on Teacher mirrors `admin`/email/name onto the linked `auth.User`. Mirroring `Teacher.admin` onto both `is_staff` and `is_superuser` is intentional — non-admin Teachers must not see `/admin/`.
- **Non-admin teacher whitelist must stay in sync** — `core/middleware.py`'s `NON_ADMIN_ALLOWED_URL_NAMES` lists every URL name a non-admin Teacher may reach. When you add a new URL pattern, decide whether non-admin Teachers should be able to hit it and update the whitelist accordingly. Forgetting this is invisible to admin testing because admins bypass the whitelist.
- **Non-admin teachers see a trimmed UI** — the home dashboard hides all financial widgets (`Pagos pendientes`, `Ingresos del mes`, pending-payments modal, `Nuevo Pago`) behind `{% if is_admin_user %}`; the sidebar hides Pagos/Gastos/Aplicaciones/Informes/Base de Datos. They CAN view the schedule (`schedule_view` is whitelisted) but it's read-only — the edit toggle is gated on `is_admin_user` and `save_schedule_slot` is NOT whitelisted (admin-only). When adding an admin-only page, gate both the sidebar link (template) and the write endpoint (whitelist).
- **Teachers created in the UI are always non-admin** — `create_teacher` (`core/views/management.py`) hard-codes `admin=False` and the management form has no admin checkbox. Only the seeded teachers (`TEACHER_SEED_*_ADMIN=True`) and the superuser are admins; an existing admin promotes others via `/admin/`. Don't re-add an admin toggle to the creation UI.
- **QA backlog tasks self-clean** — marking a `BacklogTask` done emails the admin teachers (`api_update_backlog_task`), and `core.tasks.cleanup_done_backlog_tasks` (daily Celery-beat, `project/celery.py`) deletes tasks that have been done for >30 days (keyed on `updated_at`).
- **Backlog screenshots are NEVER stored** — `api_create_backlog_task` accepts an optional screenshot (multipart, ≤5 MB, images only) and **attaches it to the notification email via `EmailMessage.attach()`** — it is read in memory and never written to disk/DB/media (deliberate, to avoid image storage growth). Don't add a model field or media upload for it. The create endpoint therefore handles BOTH multipart (with file) and JSON (no file).
- **Recurring expenses have three cadences** — `Expense.recurring_frequency` is `monthly` (uses `recurring_day` 1–28), `yearly` (`recurring_day` + `recurring_month`) or `weekly` (`recurring_weekdays`, a CSV of ints 0–6, Monday=0 matching `date.weekday()`). Monthly materialises via `materialize_recurring` (Celery beat, 1st of month); weekly + yearly via `materialize_recurring_for_date` (`materialize_recurring_expenses_daily_task`, daily 06:15). Both are idempotent (match on `generated_from` + exact `expense_date`). `Expense.clean()` validates per-frequency.
- **Keyboard nav + per-view help live in `base.js`** — number keys 0–9 navigate via `data-hotkey` attributes on the sidebar `<a>` links (0=home … 9=database); the handler ignores keypresses inside inputs/textarea/select/contenteditable and only works if the link is in the DOM (hidden admin-only links are inert for non-admins). The number badges are pure CSS (`.sidebar-link[data-hotkey]::after { content: attr(data-hotkey) }`). Each view template fills `{% block help_content %}`; `base.js` copies it into the `#view-help-modal` and reveals the bottom-left `#view-help-btn` **only** when that block is non-empty. To add help to a new view, define the block; Home's help documents the hotkeys.
- **CI deploy emails use secrets** — the dev→testing auto-merge and the main→production push both send a readable HTML email. Recipients/URLs come from GitHub secrets: `TESTING_NOTIFY_EMAILS` (support + the two admin teachers; falls back to `OWNER_EMAILS`), `TESTING_URL`, `SUPPORT_EMAIL`, optional `PRODUCTION_URL`. Never hard-code the academy's real emails in the (public) workflow files — add a secret.
- **Password reset flow is public** — `/password-reset/`, `/password-reset/sent/`, `/password-reset/confirm/<uidb64>/<token>/`, `/password-reset/complete/` are all listed in `SimpleAuthMiddleware.PUBLIC_PREFIXES` so a locked-out teacher can still reach them. The branded templates live under `project/templates/registration/` (Django's built-in views look them up by that prefix), and the HTML email uses `core/templates/emails/password_reset.html`. Gmail SMTP must work in any environment that issues real reset links.
- **Never return `str(e)` to the client; never log a request value raw** (v1.14.4 + v1.14.5 cleared 46 CodeQL alerts of these two shapes). **Catch-alls:** `except Exception` in a view must `logger.exception(...)` and return a *fixed* message — echoing exception text leaks internals (on an `IntegrityError`, the table and column names; on an `InvalidOperation`, Decimal's `ConversionSyntax` repr). Genuine validation errors are the exception: `ValidationError.messages` is Django's written-for-humans text, so keep it — but split it into its own `except` rather than sharing one with `InvalidOperation`. **Logs:** never interpolate a request-derived value raw. For ids, coerce — `logger.exception("... %d", int(payment_id))`; every id in this codebase arrives via an `<int:...>` converter, so it's a runtime no-op that breaks the taint. For free-form text, prefer not logging it at all (see the parent-portal email and `EmailService` logging `template_name` instead of `subject`) or validate it into a known shape (`ipaddress` in `rate_limit._client_ip`). `safe_log()` in `core/log_safe.py` is still there for genuinely free-form values, but see the next bullet before relying on it.
- **`safe_log()` does NOT silence CodeQL, and `# codeql[...]` comments do nothing here** — two things learned the hard way in v1.14.5. CodeQL's `py/log-injection` treats `str.replace` as taint-*preserving*, so stripping CR/LF makes the code genuinely safe but leaves the alert open (the query's own docs suggest replace-based fixes — they don't clear it). Inline `# codeql[query-id]` suppression comments are not honoured by this repo's setup either. To scope an alert out, add the path to `paths-ignore` in `.github/codeql/codeql-config.yml`, and only for code that never ships or never sees a request. Otherwise fix it structurally: coerce to `int`, validate into a known shape, or stop logging the value.
- **Known debt: `comms` reaches up into `core` twice** (deferred in v1.14.6, flagged by Copilot on PR #37). `comms/tasks.py:63` imports `core.schedule_utils.get_group_schedule_lines` (the welcome email shows the timetable, and `ScheduleSlot` is a core model) and `comms/tasks.py:685` imports `core.models.FunFridayScheduledSend` (the drain task consumes a core model). Both are lazy, function-body imports, so there is no cycle today — the cost is coupling, and it contradicts the dependency flow at the top of this file. `comms/urls.py` importing `core.views` is a separate, deliberate choice and not part of this. The fix for the first is to compute `schedule_lines` in the core request path and pass it into `send_welcome_email_task` (1 production caller, ~8 test call sites); the second probably wants the model moved to a neutral layer. Do it as its own change — fixing one site only makes the codebase inconsistent with itself.
- **`/docs` is gitignored on purpose** — `.gitignore` has `/docs` under "Docs with secrets", and the legacy folder was deleted back in v1.0.8. `docs/UV.md`, `CELERY.md`, `GITHUB.md`, `HTTPS.md` and `TODO.md` exist on the maintainer's machine only, so **every `docs/*.md` link in `README.md` and this file is a dead link on GitHub**, and anything written there is invisible to the repo and to CI. If a note needs to survive, put it in a tracked file (here, the README, or a per-app README) — not in `docs/`.
- **Django is bounded at `<7`, and 6.1 deprecates the email settings** — `pyproject.toml` pins `django>=6.0.8,<7` so a major can't land unreviewed. On 6.1 the suite emits 56 `RemovedInDjango70Warning`s: the whole `EMAIL_*` settings family is superseded by `MAILERS`, and `EmailMessage.send(fail_silently=...)` is deprecated (hit in `comms/services/email_service.py` and `core/views/testing_tools.py`). Nothing breaks before 7.0 — but that migration must happen before the bound is lifted, and it touches the entire email layer.

## README maintenance (MUST do at end of every work session)

The `README.md` must stay in sync with the code. At the end of any non-trivial change, verify and update these sections before handing off:

1. **Header badges** — version badge must match `pyproject.toml`
2. **Project Status table** — three rows in order Production → Testing → Development, each with branch + hosting + CI badge
3. **Recent Versions table** — keep only the **last 3** versions. Entries must include: version, date (YYYY-MM-DD), and an **extremely brief** description — one short phrase, ≤10 words, naming only the headline change. The long-form writeup (every user-visible change, subsection headings, bullets) lives in the Version History `<details>` block below, not in this table. When a new patch ships, drop the oldest row.
4. **Version History `<details>` blocks** — add a new `<details id="vXYZ" open>` block for the new version; remove the `open` attribute from the previous one. Structure: `**Subsection**` headings + bullet lists. Pull subjects from `git log` for the commits in that version.
5. **Directory Layout** — if directories, tool counts, test counts, or Make command counts changed, update them here. `tests/` line must show current test count and coverage percentage.
6. **Make Commands table** — every renamed or new `make` target must appear or be updated.
7. **Contributing → Development Workflow** — if the developer flow changed (new pre-commit behavior, new commands), update the numbered list.
8. **Table of Contents** — every new section or renamed heading must have a matching ToC entry with a valid anchor (GitHub generates anchors by lowercasing, replacing spaces with `-`, dropping non-alphanumerics except `-`).
9. **Delete stale content** — if a file or service was removed (e.g. `render.yaml`, a retired workflow), remove every reference to it. Grep the README for the name first.

**When the user invokes the `update-readme` skill**, use the staged changes (`git diff --cached`, `git status --porcelain`, `git diff --cached --stat`) to determine what changed, then apply the checklist above. Do not guess — inspect the staged diff first.

## Django Best Practices (enforced in this project)

### Models

- **Always set `db_table` explicitly** — prevents Django from auto-generating `appname_modelname` tables that break when models move between apps. Every model in this project has it.
- **Use string FK references for cross-app relationships** — `models.ForeignKey('students.Student', ...)` instead of importing the model directly. This avoids circular imports and makes app dependencies explicit.
- **Never put business logic in models** — models define data structure, properties, and simple computed fields (`is_overdue`, `full_name`, `age`). Complex logic (pricing, discount calculations, payment generation) lives in the service layer (`billing/services/`).
- **Use `select_related` and `prefetch_related` everywhere** — every queryset that touches related models should use these. Check `core/transactions.py` for examples. The N+1 query problem is the most common Django performance issue.
- **Use `models.Index` for frequently filtered fields** — Student is indexed on `group`, `active`, `birth_date`. Payment on `student`, `parent`, `payment_status`, `due_date`.
- **Use database constraints** — `UniqueConstraint` with conditions (e.g., one active enrollment per student), `unique_together`, and validators. Let the database enforce invariants, not just Python code.
- **Decimal, not Float, for money** — all financial fields use `DecimalField(max_digits=8, decimal_places=2)` with `MinValueValidator`. Never use `FloatField` for currency.

### Views

- **Fat services, thin views** — views handle HTTP (request parsing, response building, redirects, messages). Business logic lives in services. If a view function is doing calculations, move them to a service method.
- **Use `@require_http_methods` on FBVs** — explicitly declare which HTTP methods a view accepts. Every AJAX endpoint in this project uses this decorator.
- **Use `get_object_or_404` instead of try/except** — cleaner and returns a proper 404. Used throughout the project.
- **Wrap multi-model writes in `transaction.atomic()`** — student creation (Student + StudentParent + Enrollment + Payment) uses this. If any step fails, everything rolls back.
- **Return consistent JSON for AJAX** — always `{"success": True/False, ...}` or `{"valid": True/False, ...}`. Frontend code depends on this contract.
- **Don't import models at module level in views if it causes circular imports** — use lazy imports inside the function body when needed (see `StudentCreateView.form_valid`).

### Forms

- **Forms validate, services execute** — `EnrollmentForm.clean()` validates input. `EnrollmentService.create_enrollment()` does the actual creation. The form's `create_enrollment()` method is a thin bridge.
- **Use Django's form validation** — `clean_<field>()` methods for per-field validation, `clean()` for cross-field validation. Don't validate in views.
- **Set `input_formats` for date fields** — this project accepts both `%Y-%m-%d` (HTML5 date input) and `%d/%m/%Y` (Spanish format).

### Templates

- **Extend, don't repeat** — every authenticated page extends `base.html`. Use blocks for customization.
- **Keep templates logic-light** — complex conditionals and calculations should happen in the view's context, not in `{% if %}` chains in templates.
- **Pass Django data to JS via data attributes or config objects** — never embed `{% url %}` or `{{ variable }}` inside external JS files. Use `data-*` attributes on HTML elements or a small inline `<script>window.CONFIG = {...}</script>`.
- **All template filenames in English** — `enrollment_child.html`, not `matricula_niño.html`.

### Testing

- **Test against PostgreSQL** — SQLite has different behavior for constraints, transactions, and date handling. Always test against the same database you deploy to. Use `make test-sqlite` only for quick iteration.
- **Use fixtures in conftest.py** — shared fixtures (`student`, `parent`, `group`, `teacher`, `active_enrollment`, `pending_payment`, `authenticated_client`) avoid repetition.
- **Test at three levels** — model tests (data logic), service tests (business logic), view tests (HTTP behavior). Don't test Django internals.
- **Use `pytest.mark.django_db`** — either per-test or as `pytestmark` at module level. Without it, tests can't access the database.
- **Use `authenticated_client` fixture for view tests** — it pre-sets the session authentication that `SimpleAuthMiddleware` checks.
- **Parametrize repetitive tests** — error pages and email form views use `@pytest.mark.parametrize` to avoid duplicating test functions.

### Security

- **Never trust user input** — all form data goes through Django forms with validation. Raw `request.POST.get()` is only used for simple string values in AJAX handlers.
- **CSRF protection on every POST** — Django's `CsrfViewMiddleware` is active. JS uses `getCsrfToken()` from cookies for AJAX. The only `@csrf_exempt` is the health check.
- **Don't expose secrets in templates** — `LOGIN_PASSWORD`, API keys, etc. are never rendered in HTML. Google OAuth credentials are only used server-side.
- **Validate relationships before writes** — creating a payment validates that the student-parent relationship exists. This prevents orphaned records.
- **Use `PROTECT` for foreign keys** — deleting a Teacher won't cascade-delete their Groups. Deleting a Student won't cascade-delete Payments. Only explicit `CASCADE` where appropriate (StudentParent through model).

### Performance

- **Avoid N+1 queries** — use `select_related` (FK/OneToOne) and `prefetch_related` (M2M/reverse FK). The `transactions.py` module provides pre-built optimized querysets.
- **Don't evaluate querysets at module level** — the old `all_students = Student.objects.filter(...)` in `transactions.py` was evaluated once at import time. Now it's wrapped in functions that return fresh querysets per request.
- **Paginate large result sets** — the database view and payment list use Django's `Paginator`. The history dropdown uses offset-based AJAX pagination.
- **Use `values_list` when you only need IDs** — `student.parents.values_list('id', flat=True)` instead of fetching full Parent objects.
- **Cache the SiteConfiguration** — `SiteConfiguration.get_config()` does a single DB query per call. For request-scoped reuse, store the result in a local variable.

### Email

- **Use the EmailService class** — never call `django.core.mail.send_mail()` directly. The `EmailService` handles HTML rendering, inline images, attachments, and logging.
- **All emails have a convenience function** — `comms/services/email_functions.py` provides typed functions like `send_birthday_email(recipient, name)` instead of raw template/context calls.
- **Celery tasks for async sends** — student creation queues a welcome email via `send_welcome_email_task.delay()`. If Celery isn't available (no Redis), it runs synchronously via eager mode.
- **Always set `fail_silently=True` for non-critical emails** — a failed birthday email should not crash the application.

### Code Organization

- **Explicit imports only** — never `from app.models import *`. Every import names what it needs.
- **Cross-app references use string FKs** — `'students.Student'` in billing models, not `from students.models import Student`.
- **Keep `__init__.py` files minimal** — the `core/views/__init__.py` re-exports are an exception for URL routing compatibility. Other `__init__.py` files should be empty.
- **Group related functionality** — all pricing logic in `billing/services/`, all email logic in `comms/services/`, all view logic in `core/views/`. Don't scatter related code across apps.
- **Management commands import from services** — `generate_payments` command calls `PaymentService` methods. Commands are thin wrappers around service logic.
