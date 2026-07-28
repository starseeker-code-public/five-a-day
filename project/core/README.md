# core — Dashboard, Auth, Schedule, Shared Utilities

The `core` app is the "everything else" app — it owns the dashboard, authentication, scheduling, and lightweight cross-cutting models that don't belong to a specific domain.

## Models

| Model | Table | Purpose |
| ----- | ----- | ------- |
| **ScheduleSlot** | `schedule_slots` | Weekly schedule grid (row, day, col) with group FK |
| **FunFridayAttendance** | `fun_friday_attendance` | Tracks student attendance on Fun Fridays |
| **FunFridayScheduledSend** | `fun_friday_scheduled_sends` | Persisted Fun Friday announcements awaiting their scheduled send time (drained by `comms.tasks.send_due_fun_friday_emails_task`) |
| **TodoItem** | `todo_items` | Dashboard task list with due dates |
| **HistoryLog** | `history_logs` | Audit trail of user actions (auto-capped at 1,000 with guarded single-query cleanup) |

## Views (core/views/)

The monolithic `views.py` was split into 14 focused modules:

| Module | Views | Description |
| ------ | ----- | ----------- |
| `auth.py` | `login_view`, `logout_view`, `google_oauth_redirect`, `google_oauth_callback` | Dispatches between dev env-var basic-auth and Teacher (auth.User) login; Google OAuth backs into the same Django ModelBackend so logged-in OAuth users also get `/admin/` access |
| `password_reset.py` | `BrandedPasswordResetView`, `BrandedPasswordResetDoneView`, `BrandedPasswordResetConfirmView`, `BrandedPasswordResetCompleteView`, `build_reset_link()` | Branded subclasses of Django's built-in password-reset views; HTML email rendered via `emails/password_reset.html`; URLs exempt from the auth middleware so teachers locked out of their account can still reach them |
| `dashboard.py` | `home`, `all_info` | Dashboard with stats (single `Case/When` aggregate query), todos, birthdays, inspirational quote from zenquotes.io (48 h cookie); database view |
| `schedule.py` | `schedule_view`, `save_schedule_slot`, `fun_friday_view` | Weekly schedule grid + Fun Friday list (single attendance query for both weeks, filters from loaded students) |
| `fun_friday_attendance.py` | `toggle_fun_friday_this_week`, `add/remove_fun_friday_attendance` | AJAX attendance toggles |
| `todos.py` | `create_todo`, `complete_todo`, `history_list` | Todo CRUD + history pagination API |
| `students.py` | `StudentCreateView`, `StudentListView`, etc. | Student/parent CRUD (CBVs + FBVs) |
| `parents.py` | `ParentCreateView` | Parent creation CBV |
| `payments.py` | `payments_list`, `create_payment`, `quick_complete_payment`, etc. | Payment CRUD + AJAX APIs. Stats use single `Case/When` aggregate (1 query instead of 8). |
| `management.py` | `gestion_view`, `update_site_config`, `create_teacher`, `create_group` | Admin config panel |
| `app_forms.py` | `fun_friday_form`, `payment_reminder_form`, `newsletter_form`, `receipt_enrollment_form`, etc. | Email app form views (10+ forms, all prefill from `ACADEMY_*` env vars where relevant) |
| `support.py` | `submit_support_ticket` | Support ticket email API |
| `errors.py` | `handler400-500`, `health_check` | Error pages + health endpoint |
| `testing_tools.py` | `testing_tools_view`, `seed_testdata_ajax`, `submit_backlog`, `toggle_error_reporting` | **QA-only** dashboard at `/testing/` — database seeding, backlog reporting, error-reporting toggle. All gated by `qa_access_required` decorator. |

## URL Patterns (core/urls.py)

Routes for: login/logout, Google OAuth, password reset (request → confirm → complete), dashboard, schedule, todos, history, support, `/testing/` QA dashboard, error test pages.

Student, payment, management, and email app routes live in `students/urls.py`, `billing/urls.py`, and `comms/urls.py` respectively, but their views are still in `core/views/`.

## Middleware & Decorators

- **`SimpleAuthMiddleware`** (`middleware.py`) — two-layer access control. **Layer 1 (authentication)**: all URLs are protected except the public prefixes (`/login/`, `/health/`, `/static/`, `/media/`, `/auth/google/*`, `/password-reset/*`); unauthenticated requests are redirected to login. **Layer 2 (authorization)**: when the session user is a non-admin Teacher (`teacher.admin=False`), requests are restricted to the `NON_ADMIN_ALLOWED_URL_NAMES` whitelist — admin-only routes redirect to the dashboard with a flash message (or return 403 JSON for `/api/*` endpoints). Keep the whitelist in sync with `core/urls.py` and the per-app urls.
- **`QAErrorEmailMiddleware`** (`middleware.py`) — in the QA environment, catches unhandled exceptions and emails them to `SUPPORT_EMAIL` with the full traceback. Toggleable via the `/testing/` dashboard.
- **`qa_access_required`** (`decorators.py`) — reusable gate for `/testing/` views and endpoints. Returns 404 (not 403) unless `DJANGO_ENV=testing`, `DEBUG=False`, and the request is made by a logged-in Teacher (admin or not; resolved via `_request_teacher`). `testing_tools` + the QA API endpoints are on the non-admin whitelist so non-admin teachers can reach them too.

## Context Processor

- **`today_notifications()`** (`context_processors.py`) — injects sidebar/dashboard data into every template. In addition to `notifications_today_*` and `history_count`, it now exposes two role flags used to gate admin-only UI: **`is_admin_user`** (true for everyone except linked non-admin Teachers — dev basic-auth, OAuth, and admin Teachers all qualify) and **`is_non_admin_teacher`** (true when the session user is a linked Teacher with `admin=False`). Templates use `{% if is_admin_user %}` to hide Payments / Apps / Database from non-admin teachers and to make the Management page read-only.

## Management Commands

- **`seed_testdata`** — populates the QA database with 3 teachers, 5 groups, 6 parents, 12 child students, 3 adult students, 1 inactive student, active enrollments, payments in various states, schedule slots, todo items, and history log entries. Flags: `--reset` (wipe first), `--small` (6 children only). Also callable from the `/testing/` dashboard via AJAX.
- **`seed_teachers`** — idempotently creates Teacher rows + linked `auth.User` accounts from `TEACHER_SEED_<N>_*` env vars (N starts at 1, iteration stops at the first missing `FIRST_NAME`). Each block sets `FIRST_NAME`, `LAST_NAME`, `EMAIL` (used as the login username), and optionally `PHONE`, `ADMIN` (defaults to false), and `PASSWORD`. If `PASSWORD` is omitted the linked user gets `unusable_password` and must activate via `/password-reset/`. Re-running the command updates name/phone/admin flags and syncs the linked user but never overwrites a password an admin later changed. Runs automatically on container start when `DJANGO_ENV` is `testing` or `production` (see `entrypoint.sh`); no-op in development.
- **`cleanup_backlog_tasks`** — wraps `core.tasks.cleanup_done_backlog_tasks`: deletes QA backlog tasks marked done for more than `--days` days (default 30). For external schedulers; QA/testing environment only.
- **`export_to_sheets`** — exports students and/or payments snapshots to Google Sheets (`--students / --payments / --academic-year / --students-sheet / --payments-sheet`). No-op when the Sheets integration is unconfigured.
- **`reset_two_factor <email>`** — wipes a Teacher's TOTP secret + backup codes (recovery when both phone and codes are lost).

## Templates

All templates live in `core/templates/`:

- `base.html` — main layout (sidebar, header, support modal, Tailwind CDN config). Site-wide `<head>` metadata: `favicon.ico` + `favicon-32x32.png` + `apple-touch-icon.png` (all sourced from `logo_white_bg.png`), `theme-color` (#6d28d9), meta description/author, full Open Graph set (including `og:image:secure_url` for Facebook HTTPS), Twitter Card (including `twitter:image:alt`), and a Schema.org JSON-LD block (`WebApplication` / `EducationalOrganization`) for Google previews, Gmail, and Google Chat. Every OG/Twitter content field is wrapped in an overridable Django block so per-page templates can tailor link previews.
- `home.html`, `login.html`, `schedule.html`, `fun_friday.html`, etc. (login page renders a "¿Has olvidado tu contraseña?" link when `password_reset_available` is true, i.e. non-dev environments)
- `payments/` — payment list, create, detail
- `apps/` — email form views + `_email_preview.html` partial
- `emails/` — 18 HTML email templates extending `emails/base_email.html` (all named in English: `enrollment_child.html`, `payment_reminder.html`, `password_reset.html`, etc.). All are styled to a common standard (violet headings, rounded info cards, coloured callouts) matching `welcome_student.html`. `base_email.html` carries an inline `@media (prefers-color-scheme: dark)` stylesheet so emails render in a dark violet theme mirroring the webapp; content templates use inline `style=""` and the dark rules override those hex values with attribute selectors (the same technique as `static/css/theme.css`).
- `400.html` through `500.html` — error pages

The standalone password-reset flow uses its own template set under `project/templates/registration/` (form, done, confirm, complete, plus `reset_base.html` for shared styling and `password_reset_email.txt` / `password_reset_subject.txt` for the email body fallback). These live outside `core/templates/` because Django's built-in `PasswordResetView` looks them up by the `registration/` prefix.

## Admin Template Overrides

Project-level admin templates live in `project/templates/admin/` (added to `TEMPLATES.DIRS` so they take priority over Django's defaults):

| Template | Purpose |
| -------- | ------- |
| `base_site.html` | Violet gradient header with `logo_white_bg.png`; loads `admin_custom.css` on every admin page |
| `login.html` | Card-style login page with logo, "Gestión Académica · Albacete" subtitle, Spanish field labels |
| `index.html` | Dashboard welcome banner + Spanish Add/Edit/View/Recent-actions labels |

The matching CSS (`project/static/css/admin_custom.css`) overrides all Django admin CSS variables to the app's violet/purple palette, switches the font to Trebuchet MS, and styles the login card and welcome banner.

## Static Files

- `favicon.ico` — multi-size ICO (16/32/48/64px) generated from `images/logo_white_bg.png`
- `favicon-32x32.png` — 32×32 PNG favicon for modern browsers
- `apple-touch-icon.png` — 180×180 PNG for iOS/Safari home-screen icon
- `images/logo_white_bg.png` — 500×500 PNG with white background; used for favicon, apple-touch-icon, Open Graph, and Twitter Card
- `images/logo.png` — 500×500 PNG original (transparent background)
- `css/app.css` — sidebar transitions, Material Symbols icon font settings
- `css/admin_custom.css` — Django admin theme override (CSS variables, login card, welcome banner)
- `js/base.js` — notification/history dropdowns (loaded on every page)
- `js/support.js` — support ticket modal
- `js/home.js`, `js/students.js`, `js/payments.js`, etc. — per-page modules

## Tests

Tests for core components live in `project/tests/`:

| File | What it tests |
| ---- | ------------- |
| `test_context_processors.py` | `today_notifications()` — key presence, todo filtering, scheduled app logic, history count, support email, `is_admin_user` / `is_non_admin_teacher` flags |
| `test_middleware.py` | `SimpleAuthMiddleware` — public paths (static, health, login, oauth, password-reset), redirect behavior, authenticated sessions |
| `test_teacher_user_sync.py` | `Teacher.ensure_user()` (create + reuse + password set) and the `post_save` mirror signal (admin → is_staff/is_superuser, email/name sync) |
| `test_seed_teachers_command.py` | `manage.py seed_teachers` — creation from env vars, idempotent updates, password persistence rule, gap-stop behaviour |
| `test_teacher_auth_flow.py` *(integration)* | Login dispatcher (dev vs non-dev), non-admin teacher whitelist enforcement, dashboard role gating |
| `test_password_reset.py` *(integration)* | Full reset round-trip: request, email rendered, confirm + complete pages, public-URL middleware exemption |

Run with `make test` (requires Docker + PostgreSQL running).

## Cross-App Communication

- Imports `Student`, `Parent`, `Group` from **students**
- Imports `Payment`, `Enrollment`, `SiteConfiguration` from **billing**
- Imports `email_service`, email functions from **comms**
- Exports `HistoryLog` used by billing views for audit logging
- Exports `FunFridayAttendance` used by students views
- `constants.py` exports `DIAS_ES`, `MESES_ES`, `SCHEDULED_APPS` used by context processors and views
