# core — Dashboard, Auth, Schedule, Shared Utilities

The `core` app is the "everything else" app — it owns the dashboard, authentication, scheduling, and lightweight cross-cutting models that don't belong to a specific domain.

## Models

| Model | Table | Purpose |
| ----- | ----- | ------- |
| **ScheduleSlot** | `schedule_slots` | Weekly schedule grid (row, day, col) with group FK |
| **FunFridayAttendance** | `fun_friday_attendance` | Tracks student attendance on Fun Fridays |
| **TodoItem** | `todo_items` | Dashboard task list with due dates |
| **HistoryLog** | `history_logs` | Audit trail of user actions (auto-capped at 1,000 with guarded single-query cleanup) |

## Views (core/views/)

The monolithic `views.py` was split into 13 focused modules:

| Module | Views | Description |
| ------ | ----- | ----------- |
| `auth.py` | `login_view`, `logout_view`, `google_oauth_redirect`, `google_oauth_callback` | Session-based auth + Google OAuth |
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

Routes for: login/logout, dashboard, schedule, todos, history, support, `/testing/` QA dashboard, error test pages.

Student, payment, management, and email app routes live in `students/urls.py`, `billing/urls.py`, and `comms/urls.py` respectively, but their views are still in `core/views/`.

## Middleware & Decorators

- **`SimpleAuthMiddleware`** (`middleware.py`) — session-based auth that protects all URLs except `/login/`, `/health/`, `/static/`, `/media/`, and `/auth/google/*` (including `/callback/`). Credentials come from `LOGIN_USERNAME`/`LOGIN_PASSWORD` env vars (required; no hardcoded fallbacks).
- **`QAErrorEmailMiddleware`** (`middleware.py`) — in the QA environment, catches unhandled exceptions and emails them to `SUPPORT_EMAIL` with the full traceback. Toggleable via the `/testing/` dashboard.
- **`qa_access_required`** (`decorators.py`) — reusable gate for `/testing/` views and endpoints. Returns 404 (not 403) unless `DJANGO_ENV=testing`, `DEBUG=False`, and the session user matches `QA_TESTING_USERNAME`.

## Management Commands

- **`seed_testdata`** — populates the QA database with 3 teachers, 5 groups, 6 parents, 12 child students, 3 adult students, 1 inactive student, active enrollments, payments in various states, schedule slots, todo items, and history log entries. Flags: `--reset` (wipe first), `--small` (6 children only). Also callable from the `/testing/` dashboard via AJAX.

## Templates

All templates live in `core/templates/`:

- `base.html` — main layout (sidebar, header, support modal, Tailwind CDN config). Site-wide `<head>` metadata: `favicon.ico` + `favicon-32x32.png` + `apple-touch-icon.png` (all sourced from `logo_white_bg.png`), `theme-color` (#6d28d9), meta description/author, full Open Graph set (including `og:image:secure_url` for Facebook HTTPS), Twitter Card (including `twitter:image:alt`), and a Schema.org JSON-LD block (`WebApplication` / `EducationalOrganization`) for Google previews, Gmail, and Google Chat. Every OG/Twitter content field is wrapped in an overridable Django block so per-page templates can tailor link previews.
- `home.html`, `login.html`, `schedule.html`, `fun_friday.html`, etc.
- `payments/` — payment list, create, detail
- `apps/` — email form views + `_email_preview.html` partial
- `emails/` — 12 HTML email templates extending `emails/base_email.html` (all named in English: `enrollment_child.html`, `payment_reminder.html`, etc.)
- `400.html` through `500.html` — error pages

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
| `test_context_processors.py` | `today_notifications()` — key presence, todo filtering, scheduled app logic, history count, support email |
| `test_middleware.py` | `SimpleAuthMiddleware` — public paths (static, health, login, oauth), redirect behavior, authenticated sessions |

Run with `make test` (requires Docker + PostgreSQL running).

## Cross-App Communication

- Imports `Student`, `Parent`, `Group` from **students**
- Imports `Payment`, `Enrollment`, `SiteConfiguration` from **billing**
- Imports `email_service`, email functions from **comms**
- Exports `HistoryLog` used by billing views for audit logging
- Exports `FunFridayAttendance` used by students views
- `constants.py` exports `DIAS_ES`, `MESES_ES`, `SCHEDULED_APPS` used by context processors and views
