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
| **BacklogTask** | `backlog_tasks` | QA-only bug/feature reports filed from the `/testing/` dashboard. Marking one done emails the admin teachers; `core.tasks.cleanup_done_backlog_tasks` deletes rows done > 30 days ago. `verified` (v1.20.0) is the **tester's** tick — deliberately independent of `status="done"`, which is the developer's and sends the notification |
| **QAConfiguration** | `qa_configuration` | QA-only singleton holding the error-email toggle |
| **AuditLog** | `audit_logs` | v1.10 immutable per-model change trail (`audit_models.py`), written by the `post_save`/`post_delete` receivers in `audit_signals.py` with a contextvar-supplied actor |

## Views (core/views/)

The monolithic `views.py` was split into **22** focused modules:

| Module | Views | Description |
| ------ | ----- | ----------- |
| `auth.py` | `login_view`, `logout_view`, `google_oauth_redirect`, `google_oauth_callback` | Dispatches between dev env-var basic-auth and Teacher (auth.User) login; Google OAuth backs into the same Django ModelBackend so logged-in OAuth users also get `/admin/` access |
| `password_reset.py` | `BrandedPasswordResetView`, `BrandedPasswordResetDoneView`, `BrandedPasswordResetConfirmView`, `BrandedPasswordResetCompleteView`, `build_reset_link()` | Branded subclasses of Django's built-in password-reset views; HTML email rendered via `emails/password_reset.html`; URLs exempt from the auth middleware so teachers locked out of their account can still reach them |
| `dashboard.py` | `home`, `all_info` | Dashboard with stats (single `Case/When` aggregate query), todos, birthdays, inspirational quote from zenquotes.io (48 h cookie); database view |
| `schedule.py` | `schedule_view`, `save_schedule_slot`, `fun_friday_view` | Weekly schedule grid + Fun Friday list (single attendance query for both weeks, filters from loaded students) |
| `fun_friday_attendance.py` | `toggle_fun_friday_this_week`, `add/remove_fun_friday_attendance` | AJAX attendance toggles |
| `todos.py` | `create_todo`, `complete_todo`, `history_list` | Todo CRUD + history pagination API |
| `students.py` | `StudentCreateView`, `StudentListView`, `search_students`, etc. | Student/parent CRUD (CBVs + FBVs). `search_students` returns the student's **parent** in the same response (v1.20.0) — the create-payment form used to follow up with a POST to `validate_student_parent` carrying `parent_id: 0` behind a bare `.catch(() => {})`, so any failure left "Padre/Tutor" silently blank. `parent_id` is `None` for an adult student, which is valid. |
| `parents.py` | `ParentCreateView` | Parent creation CBV |
| `payments.py` | `payments_list`, `create_payment`, `quick_complete_payment`, etc. | Payment CRUD + AJAX APIs. Stats use single `Case/When` aggregate (1 query instead of 8). |
| `management.py` | `gestion_view`, `update_site_config`, `create_teacher`, `create_group` | Admin config panel |
| `app_forms.py` | `fun_friday_form`, `payment_reminder_form`, `newsletter_form`, `receipt_enrollment_form`, etc. | Email app form views (10+ forms, all prefill from `ACADEMY_*` env vars where relevant) |
| `support.py` | `submit_support_ticket` | Support ticket email API |
| `errors.py` | `handler400-500`, `health_check`, `_database_probe` | Error pages + health endpoint. `/health/` is shallow (version + environment, no DB); `/health/?deep=1` adds DB connectivity and migration counts, returns 503 when unreachable, and includes row counts only for a caller sending a valid `X-Probe-Token` (`HEALTH_PROBE_TOKEN`) |
| `testing_tools.py` | `testing_tools_view`, `api_seed_database`, `api_create_backlog_task`, `api_update_backlog_task`, `api_toggle_error_email`, `api_mark_ready`, `export_backlog_tasks` (v1.15), `_backlog_tasks_qs` (v1.20.0) | **QA-only** dashboard at `/testing/` — database seeding, backlog reporting + JSON/CSV export (`?format=csv`, `?scope=all`), error-reporting toggle. All gated by `qa_access_required` (admin Teachers only). Backlog screenshots are attached to the notification email and **never stored** on disk or in the DB. v1.20.0: the dashboard and the export share `_backlog_tasks_qs()`, which annotates `Q(status="done")` and orders `is_done, -created_at` so unfinished work comes first (the model's own `Meta.ordering` is `-created_at` alone, and with the list capped at 50 a done ticket pushed live ones off the page); and a payload carrying only `verified` toggles QA's tick and returns early, never touching `status` nor firing `_email_task_done()`. |
| `waiting_list.py` | `waiting_list_view`, `waiting_list_create` (v1.15), `assign_from_waiting_list`, `add_to_waiting_list`, `_waiting_students_qs`, `waiting_entry_from_request`, `discard_waiting_entry`, `group_capacity_summary`, `notify_capacity_freed` | v1.1 — waiting list + `Group.max_students` capacity. v1.15 adds a short create form needing only a name and a phone number, and `add_to_waiting_list` **cancels** the active enrollment — leaving it active kept the student being billed and made the later promotion violate `unique_active_enrollment_per_student`, so they could never come back off the list. v1.17.2: `assign_from_waiting_list` no longer enrolls in place (a waiting entry has no `Parent`, so it produced an active student with no titular) — it checks the cap and redirects to `parent_create?from_waiting=<id>`, and the normal `StudentCreateView` does the enrollment. v1.20.0: `_waiting_students_qs()` orders `-waiting_priority, waiting_since, created_at`, so the **Prioritario** flag actually jumps the FIFO queue instead of being a decorative badge. |
| `sheets.py` | `export_to_sheets` | v1.2 — pushes student/payment snapshots to Google Sheets; returns 503 when the integration is unconfigured |
| `expenses.py` | `expenses_list`, `create_expense`, `update_expense` (v1.20.0), `delete_expense` | v1.5 — expense CRUD with the three recurring cadences (monthly / weekly / yearly). v1.20.0 adds the missing **edit** path: `create_expense` and `update_expense` share `_expense_fields_from()` so a cadence can never be parsed two ways, both call `full_clean()` (nothing else runs `Expense.clean()`, so an invalid recurrence used to persist and then never materialise), and an unknown `category` falls back to `other`. Rows already materialised are **not** rewritten — they are what the academy actually paid. `_default_expense_date()` prefills the create form with a real `YYYY-MM-DD` value; it used to be fed `"{{ month }}-{{ year }}"`, which `<input type="date">` rejects, so the field always rendered blank |
| `reports.py` | `reports_view`, `reports_pdf` | v1.7 — analytics dashboard (financial summary, collection rate, retention, group utilisation) + reportlab PDF export |
| `parent_portal.py` | `parent_portal_login`, `parent_portal_verify`, `parent_portal_logout`, `parent_portal_dashboard`, `parent_portal_payments`, `parent_portal_receipt`, `parent_portal_tax_certificate` | v1.9 — parent-facing surface with passwordless magic-link auth (`ParentSessionToken`, single-use under `SELECT FOR UPDATE`). Scoped to that parent's own children. Login is rate-limited and never reveals whether an email is registered. |
| `stripe_views.py` | `create_checkout_link`, `stripe_webhook` | v1.11 — Stripe Checkout for the parent portal. `STRIPE_WEBHOOK_SECRET` is **required in production**: unset, the webhook skips signature verification. |
| `pwa.py` | `web_manifest`, `service_worker` | v1.12 — installable-app manifest + cache-first service worker for hashed static |
| `two_factor.py` | `two_factor_setup`, `two_factor_manage`, `two_factor_verify` | v1.13 — TOTP 2FA for admin Teachers (`pyotp` + `qrcode`), backup codes, and the mid-login verification gate |

## Services (core/services/)

| Module | Purpose |
| ------ | ------- |
| `analytics_service.py` | v1.7 — financial summary, collection rate, retention snapshot, group utilisation, and the composed dashboard report used by `reports.py` |
| `google_sheets_service.py` | v1.2 — Sheets export. Configured by either `GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON` (inline, for Secret Manager) or `..._FILE` (path), plus `GOOGLE_SHEETS_SPREADSHEET_ID`. Dormant and harmless when unset. |
| `two_factor_service.py` | v1.13 — TOTP enrolment/verification, backup-code generation, rotation, and disable |

## URL Patterns (core/urls.py)

Routes for: login/logout, Google OAuth, password reset (request → confirm → complete), dashboard, schedule, todos, history, support, `/testing/` QA dashboard, error test pages.

Student, payment, management, and email app routes live in `students/urls.py`, `billing/urls.py`, and `comms/urls.py` respectively, but their views are still in `core/views/`.

## Middleware & Decorators

- **`SimpleAuthMiddleware`** (`middleware.py`) — two-layer access control. **Layer 1 (authentication)**: all URLs are protected except the public prefixes (`/login/`, `/health/`, `/static/`, `/media/`, `/auth/google/*`, `/password-reset/*`); unauthenticated requests are redirected to login. **Layer 2 (authorization)**: when the session user is a non-admin Teacher (`teacher.admin=False`), requests are restricted to the `NON_ADMIN_ALLOWED_URL_NAMES` whitelist — admin-only routes redirect to the dashboard with a flash message (or return 403 JSON for `/api/*` endpoints). Keep the whitelist in sync with `core/urls.py` and the per-app urls.
- **`QAErrorEmailMiddleware`** (`middleware.py`) — in the QA environment, catches unhandled exceptions and emails them to `SUPPORT_EMAIL` with the full traceback. Toggleable via the `/testing/` dashboard.
- **`NoHtmlCacheMiddleware`** (`middleware.py`) — sets `Cache-Control: no-cache, no-store, must-revalidate` on every `text/html` response. Content-hashed static assets are served `immutable` for 10 years, so a browser-cached HTML page would pin *old* asset hashes and leave the theme stale after a deploy. Sits right after WhiteNoise in `MIDDLEWARE`.
- **`AuditActorMiddleware`** (`audit_signals.py`) — stashes the current user in a contextvar so the `AuditLog` signal receivers can attribute a change without threading the request through the ORM.
- **`qa_access_required`** (`decorators.py`) — reusable gate for `/testing/` views and endpoints. Returns 404 (not 403) unless `IS_TESTING_ENV` **and** the request comes from a logged-in Teacher with `admin=True` (resolved via `_request_teacher`). Non-admin teachers must not reach the dev tools (DB seed/reset, error-email toggle, git internals), so they get a 404 and `show_testing_tools` hides the sidebar icon from them. Because the QA routes are admin-only, they are deliberately **absent** from `NON_ADMIN_ALLOWED_URL_NAMES` — admins bypass that whitelist anyway.

## Logging Helpers

- **`safe_log(value, max_len=200)`** (`log_safe.py`, v1.14.4) — single-line, length-capped rendering of anything user-controlled that is about to be formatted into a log record. Strips `CR`/`LF`/`VT`/`FF`/`ESC` so an attacker-supplied field can't forge extra log lines (CodeQL `py/log-injection`) or smuggle terminal escapes into a tailed log, and truncates at 200 chars so one field can't flood the log. Stdlib-only leaf module — no Django, no models — so any app can import it without a cycle. **Use it for every log call whose value comes from a request** (headers, query string, form body, URL captures). `comms/services/email_service.py` carries a module-private twin (`_safe_log`) instead of importing this, because `comms` must not depend on `core`. **Note (v1.14.5):** this does not silence CodeQL's `py/log-injection` — the query treats `str.replace` as taint-preserving. For ids, coerce with `int()` and `%d`; for free-form values, prefer not logging them or validating them into a known shape (see `_client_ip`).

## Context Processor

- **`today_notifications()`** (`context_processors.py`) — injects sidebar/dashboard data into every template. In addition to `notifications_today_*` and `history_count`, it now exposes two role flags used to gate admin-only UI: **`is_admin_user`** (true for everyone except linked non-admin Teachers — dev basic-auth, OAuth, and admin Teachers all qualify) and **`is_non_admin_teacher`** (true when the session user is a linked Teacher with `admin=False`). Templates use `{% if is_admin_user %}` to hide Payments / Apps / Database from non-admin teachers and to make the Management page read-only.

## Management Commands

- **`prune_audit_log`** (v1.15) — deletes `AuditLog` rows older than `--days` (default two years). Wraps `core.tasks.prune_audit_log` so Cloud Scheduler can run it as a Cloud Run Job; production has no Celery Beat. The audit trail has no row cap and writes ~16 rows per student per academic year.
- **`seed_testdata`** — populates the QA database with 3 teachers, 5 groups, 6 parents, 12 child students, 3 adult students, 1 inactive student, active enrollments, payments in various states, schedule slots, todo items, and history log entries. Flags: `--reset` (wipe first), `--small` (6 children only). Also callable from the `/testing/` dashboard via AJAX.
- **`seed_teachers`** — idempotently creates Teacher rows + linked `auth.User` accounts from `TEACHER_SEED_<N>_*` env vars (N starts at 1, iteration stops at the first missing `FIRST_NAME`). Each block sets `FIRST_NAME`, `LAST_NAME`, `EMAIL` (used as the login username), and optionally `PHONE`, `ADMIN` (defaults to false), and `PASSWORD`. If `PASSWORD` is omitted the linked user gets `unusable_password` and must activate via `/password-reset/` — which works because `ActivationFriendlyPasswordResetForm` overrides Django's default of skipping unusable-password users (before v1.15 that flow silently emailed nobody). Re-running the command updates name/phone/admin flags and syncs the linked user but never overwrites a password an admin later changed. Runs automatically on container start when `DJANGO_ENV` is `testing` or `production` (see `entrypoint.sh`); no-op in development.
- **`cleanup_backlog_tasks`** — wraps `core.tasks.cleanup_done_backlog_tasks`: deletes QA backlog tasks marked done for more than `--days` days (default 30). For external schedulers; QA/testing environment only.
- **`export_to_sheets`** — exports students and/or payments snapshots to Google Sheets (`--students / --payments / --academic-year / --students-sheet / --payments-sheet`). No-op when the Sheets integration is unconfigured.
- **`reset_two_factor <email>`** — wipes a Teacher's TOTP secret + backup codes (recovery when both phone and codes are lost).

## Templates

All templates live in `core/templates/`:

- `base.html` — main layout (sidebar, header, support modal, Tailwind CDN config). Site-wide `<head>` metadata: `favicon.ico` + `favicon-32x32.png` + `apple-touch-icon.png` (all sourced from `logo_white_bg.png`), `theme-color` (#6d28d9), meta description/author, full Open Graph set (including `og:image:secure_url` for Facebook HTTPS), Twitter Card (including `twitter:image:alt`), and a Schema.org JSON-LD block (`WebApplication` / `EducationalOrganization`) for Google previews, Gmail, and Google Chat. Every OG/Twitter content field is wrapped in an overridable Django block so per-page templates can tailor link previews.
- `home.html`, `login.html`, `schedule.html`, `fun_friday.html`, etc. (login page renders a "¿Has olvidado tu contraseña?" link when `password_reset_available` is true, i.e. non-dev environments)
- `payments/` — payment list, create, detail
- `apps/` — email form views + `_email_preview.html` partial
- `emails/` — 18 HTML content email templates extending `emails/base_email.html` (19 files in the directory including the base itself) (all named in English: `enrollment_child.html`, `payment_reminder.html`, `password_reset.html`, etc.). All are styled to a common standard (violet headings, rounded info cards, coloured callouts) matching `welcome_student.html`. `base_email.html` carries an inline `@media (prefers-color-scheme: dark)` stylesheet so emails render in a dark violet theme mirroring the webapp; content templates use inline `style=""` and the dark rules override those hex values with attribute selectors (the same technique as `static/css/theme.css`).
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
- `images/happy-birthday.png` — illustration inlined into the birthday email (v1.20.0)
- `images/divider.svg` — section divider used in the email templates
- `css/app.css` — sidebar transitions, Material Symbols icon font settings
- `css/theme.css` — the **entire dark theme**, written as `html.dark .<utility>` overrides (not Tailwind `dark:` variants). Add an override here whenever a template gains a new surface/text utility or a `primary-*` shade.
- `css/email.css` — shared email styling reference
- `css/admin_custom.css` — Django admin theme override (CSS variables, login card, welcome banner)
- **17 JS modules** in `js/`: `base.js` (notification/history dropdowns, keyboard nav via `data-hotkey`, per-view help modal, `window.CSRF_TOKEN`), `theme.js` (toggles `html.dark`), `support.js`, `home.js`, `students.js`, `student-create.js`, `student-detail.js`, `payments.js`, `schedule.js`, `fun-friday.js`, `expenses.js` (create-form recurrence toggling + the v1.20.0 edit modal, whose action URL is built by swapping the id into the `{% url 'update_expense' 0 %}` template rendered on the modal), `management.js`, `all-info.js`, `app-forms.js`, `login_effects.js`, `login_seasonal.js`, `password_toggle.js` (hold-to-reveal eye button for any `[data-password-toggle]`)

## Tests

Tests for core components live in `project/tests/`:

| File | What it tests |
| ---- | ------------- |
| `test_context_processors.py` | `today_notifications()` — key presence, todo filtering, scheduled app logic, history count, support email, `is_admin_user` / `is_non_admin_teacher` flags |
| `test_schedule_utils.py` | `slot_time_range()` + `get_group_schedule_lines()` — row/day band mapping, Friday override, duplicate-column collapsing, day ordering, out-of-range days, group isolation |
| `test_log_safe.py` | `safe_log()` — line-break and `ESC` stripping, non-string coercion, truncation marker, `max_len` override, boundary at exactly the limit |
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
