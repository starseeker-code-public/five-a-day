# Five a Day eVolution

<p align="center">
  <img src="project/core/static/images/logo_white_bg.png" alt="Five a Day Logo" width="320">
  <br>
  <em>Student Management System for Five a Day English Academy</em>
  <br>
  <em>Albacete, Spain</em>
</p>

<p align="center">
  <a href="https://github.com/starseeker-code-public/five-a-day/actions/workflows/ci.yml"><img src="https://github.com/starseeker-code-public/five-a-day/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="https://codecov.io/gh/starseeker-code-public/five-a-day"><img src="https://codecov.io/gh/starseeker-code-public/five-a-day/branch/main/graph/badge.svg" alt="Coverage"></a>
</p>

---

Built to centralize student records, automate billing cycles, and streamline parent communication for a small and lovely English academy.

### Project Status

<p align="center">
  <img src="https://img.shields.io/badge/version-v1.17.0-brightgreen?style=flat-square" alt="Version">
  &nbsp;|&nbsp;
  <a href="https://github.com/starseeker-code-public/five-a-day/actions/workflows/ci.yml?query=branch%3Amain"><img src="https://github.com/starseeker-code-public/five-a-day/actions/workflows/ci.yml/badge.svg?branch=main&style=flat-square" alt="CI main"></a>
  &nbsp;|&nbsp;
  <a href="https://codecov.io/gh/starseeker-code-public/five-a-day"><img src="https://codecov.io/gh/starseeker-code-public/five-a-day/branch/main/graph/badge.svg" alt="Coverage"></a>
  &nbsp;|&nbsp;
  <a href="https://github.com/starseeker-code-public/five-a-day/actions/workflows/scorecard.yml"><img src="https://img.shields.io/badge/OpenSSF%20Scorecard-monitored-blueviolet?style=flat-square" alt="OSSF Scorecard"></a>
  &nbsp;|&nbsp;
  <a href="https://github.com/starseeker-code-public/five-a-day/security/dependabot"><img src="https://img.shields.io/badge/Dependabot-enabled-025E8C?style=flat-square&logo=dependabot" alt="Dependabot"></a>
</p>


| Environment | Branch | Hosting | CI Status |
|-------------|--------|---------|-----------|
| **Production** | `main` | [https://fiveaday-332600671945.europe-southwest1.run.app/login/](https://fiveaday-332600671945.europe-southwest1.run.app/login/) — GCP Cloud Run + Cloud SQL (PostgreSQL 16, `europe-southwest1`) | [![Production CI](https://github.com/starseeker-code-public/five-a-day/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/starseeker-code-public/five-a-day/actions/workflows/ci.yml?query=branch%3Amain) |
| **Testing (QA)** | `testing` | [http://34.26.130.187:8000/](http://34.26.130.187:8000/) — GCP Compute Engine `e2-micro` (always-free tier, Docker Compose) | [![Testing CI](https://github.com/starseeker-code-public/five-a-day/actions/workflows/ci.yml/badge.svg?branch=testing)](https://github.com/starseeker-code-public/five-a-day/actions/workflows/ci.yml?query=branch%3Atesting) |
| **Development** | `development` | [Local Docker](http://localhost:8000/) via `make up` | [![Development CI](https://github.com/starseeker-code-public/five-a-day/actions/workflows/ci.yml/badge.svg?branch=development)](https://github.com/starseeker-code-public/five-a-day/actions/workflows/ci.yml?query=branch%3Adevelopment) |


| Version | Date | Description |
|---------|------|-------------|
| **v1.17.0** | 2026-08-30 | Hold-to-reveal eye button on the login password |
| v1.16.0 | 2026-08-27 | Deep health probe, verified backups, tiered retention |
| v1.15.2 | 2026-08-27 | LF line-ending normalisation, unblocks the release PR |

---

## Table of Contents

- [Five a Day eVolution](#five-a-day-evolution)
    - [Project Status](#project-status)
  - [Table of Contents](#table-of-contents)
  - [Version History \& Roadmap](#version-history--roadmap)
    - [Roadmap](#roadmap)
  - [Tech Stack](#tech-stack)
    - [Backend](#backend)
    - [Frontend](#frontend)
    - [Infrastructure \& Deployment](#infrastructure--deployment)
    - [Python Dependencies](#python-dependencies)
    - [Developer Tooling](#developer-tooling)
  - [Database Schema](#database-schema)
    - [ER Diagram](#er-diagram)
    - [Key Constraints](#key-constraints)
  - [Development \& Docker](#development--docker)
    - [Quick Start](#quick-start)
    - [.env template](#env-template)
    - [Make Commands](#make-commands)
    - [Environment Configuration](#environment-configuration)
    - [Environment Variables Reference](#environment-variables-reference)
    - [App Versioning](#app-versioning)
  - [Project Structure \& Architecture](#project-structure--architecture)
    - [Architecture Overview](#architecture-overview)
    - [App Dependency Flow](#app-dependency-flow)
    - [Directory Layout](#directory-layout)
    - [App: core](#app-core)
    - [App: students](#app-students)
    - [App: billing](#app-billing)
    - [App: comms](#app-comms)
    - [Design Decisions](#design-decisions)
  - [Features by View](#features-by-view)
    - [Home (Dashboard)](#home-dashboard)
    - [Students](#students)
    - [Student Create](#student-create)
    - [Student Detail \& Update](#student-detail--update)
    - [Payments](#payments)
    - [Expenses](#expenses)
    - [Reports](#reports)
    - [Schedule](#schedule)
    - [Fun Friday](#fun-friday)
    - [Waiting List](#waiting-list)
    - [Apps (Email Tools)](#apps-email-tools)
    - [Management](#management)
    - [Database (All Info)](#database-all-info)
    - [Login](#login)
    - [Password Reset](#password-reset)
    - [Two-Factor Authentication](#two-factor-authentication)
    - [Parent Portal](#parent-portal)
    - [PWA (Installable App)](#pwa-installable-app)
  - [Testing](#testing)
    - [Testing Overview](#testing-overview)
    - [Unit Tests](#unit-tests)
    - [Integration Tests](#integration-tests)
    - [Coverage Report](#coverage-report)
  - [Migrations](#migrations)
  - [Security](#security)
    - [Authentication](#authentication)
    - [Session \& Cookie Configuration](#session--cookie-configuration)
    - [CSRF Protection](#csrf-protection)
    - [Transport Security (HTTPS)](#transport-security-https)
    - [Security Headers](#security-headers)
    - [Infrastructure \& Deployment](#infrastructure--deployment-1)
      - [Docker](#docker)
      - [Google Cloud Run](#google-cloud-run)
      - [Cold-start behaviour on Cloud Run](#cold-start-behaviour-on-cloud-run)
    - [Secrets Management](#secrets-management)
    - [Email Security](#email-security)
    - [Data Protection \& Input Validation](#data-protection--input-validation)
    - [Logging \& Monitoring](#logging--monitoring)
    - [Future Security Improvements](#future-security-improvements)
  - [Testing Environment (QA)](#testing-environment-qa)
    - [What is the testing environment?](#what-is-the-testing-environment)
    - [How to access it](#how-to-access-it)
    - [What you can test](#what-you-can-test)
    - [How to report a problem](#how-to-report-a-problem)
    - [Error pages you might see](#error-pages-you-might-see)
    - [For developers: how the QA environment works](#for-developers-how-the-qa-environment-works)
      - [Access control for `/testing/`](#access-control-for-testing)
  - [CI/CD \& GitHub Actions](#cicd--github-actions)
    - [Pipeline Overview](#pipeline-overview)
    - [Branch Strategy](#branch-strategy)
    - [Workflows](#workflows)
    - [Automated Flows](#automated-flows)
    - [Branch Protection — `main`](#branch-protection--main)
    - [Branch Protection — `testing`](#branch-protection--testing)
    - [Public Repository Hardening](#public-repository-hardening)
    - [Required GitHub Secrets](#required-github-secrets)
    - [Email Notifications](#email-notifications)
    - [Dependabot](#dependabot)
    - [CodeQL Security Scanning](#codeql-security-scanning)
  - [Contributing](#contributing)
    - [Development Workflow](#development-workflow)
    - [Make Commands (Developer Tooling)](#make-commands-developer-tooling)
    - [Code Conventions](#code-conventions)
    - [Adding a Feature](#adding-a-feature)
  - [License](#license)

---

## Version History & Roadmap

<details id="v1170" open>
<summary><strong>v1.17.0 — Hold-to-reveal password on the login page (current)</strong></summary>

**What prompted it**

- Typing a password blind into the login form is the one place in the app where a typo
  costs a full round-trip and, after enough tries, the login rate limit. Teachers asked
  for the standard eye button.

**Login page**

- Added an eye button inside the password field's `.input-wrap`. The password is shown as
  plain text only **while the button is held down** — pointer or keyboard — and is re-masked
  the instant it is released, the pointer leaves the button, focus is lost, the window is
  blurred, or the form is submitted. A plain click never leaves the value on screen.
- The icon swaps between `visibility` and `visibility_off` and the button carries
  `aria-pressed` so screen readers announce the current state. It sits in the tab order
  after the password input.
- Styled to match the login card's standalone palette, with `html.dark` overrides beside
  the existing `.input-wrap` dark rules — `login.html` carries its own CSS and is not
  covered by `theme.css`.

**New file**

- `core/static/js/password_toggle.js` — binds any
  `<button data-password-toggle="<input id>">` to the input with that id, so the same
  hold-to-reveal behaviour can be dropped onto other password fields without new JS.

</details>

<details id="v1160">
<summary><strong>v1.16.0 — Deep health probe, verified backups, tiered retention</strong></summary>

**What prompted it**

- A testing deploy reported the correct version while serving a **months-old database**. The
  code deployed fine; only the DB volume was wrong, and nothing in the pipeline could see it.
  Root cause: the VM stack was brought up with only `docker-compose.yml`, but
  `docker-compose.testing.yml` overrides the `db` service to mount `testing_postgres_data`
  instead of the base `postgres_data`. A one-file bring-up starts a valid stack on the dev
  volume and exits 0. The real data was orphaned, not deleted, and was recovered intact.

**Health endpoint**

- `/health/` gains an opt-in deep probe at **`/health/?deep=1`** reporting database
  connectivity plus applied and unapplied migration counts, and returning **503** when the
  database is unreachable. The default response stays shallow and never touches the database,
  so liveness checks cannot flap on a transient blip.
- Row counts identify *which* database is in use, so they are returned only to a caller
  presenting `X-Probe-Token` matching the new `HEALTH_PROBE_TOKEN`, compared with
  `constant_time_compare`. `/health/` is public; an unset token disables counts entirely.
- Exceptions are logged, never echoed to the client.

**Production backups**

- Retention is now tiered: 7 nightly automated backups (native), plus one `tier:biweekly` and
  one `tier:monthly` on-demand backup, with manual/deploy backups capped at the 3 most recent.
  Cloud SQL has no grandfather-father-son option, so `scripts/backup_retention.sh` builds the
  longer tiers from on-demand backups, which are exempt from the automated retention count.
- `scripts/export_prod_db.sh` produces a full logical `.sql.gz` export to a directory the
  operator names — **required argument, no default**. It stages through a private bucket,
  verifies the archive, then deletes the cloud copy. The script is **gitignored and never
  pushed**, because the dump contains personal data for real students including minors.
- `make backup` is documented as **local dev only**; it never contacts Cloud SQL.

**Deploy skill**

- Both compose files are now mandatory for the testing VM, enforced by a hard gate that
  inspects the mounted volume and aborts on anything but `*testing_postgres_data`.
- A backup health check runs **before anything else**, and the production backup must be
  verified `SUCCESSFUL` before any migration or rollout.
- New reconciliation step compares a pre/post deep-probe fingerprint; any drop in a row count
  stops the deploy and asks whether to roll back the revision or restore the backup.
- The production build now asserts `HEAD == origin/main` before `gcloud builds submit`, which
  uploads the working tree rather than the branch.

**Testing**

- 8 new tests covering the shallow/deep split, token gating (absent, wrong, empty, valid), the
  503 degraded path, and the guarantee that exception text never reaches the client. Suite at
  **1,213 tests, 95.30% coverage**.

</details>

<details id="v1152">
<summary><strong>v1.15.2 — LF line-ending normalisation</strong></summary>

**Repository hygiene**

- `.gitattributes` only ever managed `*.sh`, so every other text file took whatever
  line ending the committing checkout happened to use. v1.15.0 was authored on Windows
  and committed **CRLF** into `DEPLOYMENT.md`, the four app READMEs, `settings.py` and
  ~25 other files that `main` still held as **LF**. Git compares line by line, so every
  line of those files read as modified on both sides at once — which is why the
  `testing → main` release PR (#40) conflicted across whole files rather than at the
  handful of lines that actually changed.
- `.gitattributes` now sets `* text=auto eol=lf`, with images and other binaries pinned
  `binary` so auto-detection can never rewrite them. The tree was renormalised with
  `git add --renormalize .`; the only files still holding `0x0D` bytes are the PNG/ICO
  assets, where those bytes are image data. Working-tree endings on Windows are
  unaffected for editing.

**Testing**

- `TestSingletonsResistDeletion.test_instance_delete_returns_djangos_tuple` called
  `site_config.delete()` *inside* its `assert`. Under `python -O` assertions are stripped
  and the deletion the test exists to exercise would vanish with them; the call now
  happens on its own line. Flagged by CodeQL on PR #40.

</details>

<details id="v1151">
<summary><strong>v1.15.1 — Portable test paths + dependency bump</strong></summary>

**Testing**

- Two XSS-regression guards in `integration/test_bugfix_security_and_features.py` read their JS
  source through a hard-coded `/app/project/...` container path, so they failed with
  `FileNotFoundError` anywhere the repo wasn't mounted at `/app`. Both now resolve the file through
  `settings.BASE_DIR`, which is correct in Docker, CI and a local checkout alike. The assertions
  themselves are unchanged — the sinks are still pinned.

**Dependencies**

- `gunicorn` bound raised from `<24` to `<27` (Dependabot #39).

</details>

<details id="v1150">
<summary><strong>v1.15.0 — Security &amp; billing audit + backlog delivery</strong></summary>

A full-codebase audit followed by a fix pass. The suite was green at 1,061 tests and 95 % coverage
throughout, and `ruff`, `mypy` and `bandit` all passed — none of the defects below were caught by
any of them, because the suite measured *lines executed* rather than *behaviour observed*. Each fix
now has a regression test that asserts what the user sees, what lands in the database, or what gets
emailed.

**Stored XSS — three sinks, all reachable by a non-admin teacher**

- `base.js` rendered `HistoryLog.message` into `innerHTML` unescaped. `complete_todo` interpolates the todo title verbatim, and both `create_todo` and `complete_todo` are on the non-admin whitelist — so a non-admin could plant a payload that executed in an **admin's** browser on every page, since `base.js` loads globally. Added `escapeHtml()` and applied it to message, icon and timestamp
- `payments.js` built the student autocomplete with `innerHTML` plus an inline `onclick="…('${s.full_name}')"`, escaping only single quotes; a name containing a double quote broke out of the attribute. Rebuilt as DOM nodes with `textContent` and `addEventListener`
- `schedule.html` inlined `{{ groups_json|safe }}` inside `<script>`. `json.dumps` does not escape `</script>`, so a student or group name could terminate the block. Switched to `|json_script`; `schedule_view` now passes objects instead of pre-serialised strings
- `schedule.js`'s `esc()` did not escape quotes, and `esc(g.color)` lands inside a double-quoted `style` attribute — now escapes `"` and `'`

**Authentication &amp; sessions**

- **Login rate limiting was a no-op.** `_client_ip()` read `X-Forwarded-For.split(",")[0]`, which is client-supplied — a proxy *appends* what it saw, so the leftmost entry is attacker-controlled. Rotating it gave every request a fresh bucket: 12 login attempts, 0 throttled, against a documented limit of 5/min. Now reads `TRUSTED_PROXY_COUNT` hops from the right
- Added `CACHE_URL`: the limiter is cache-backed and `LocMemCache` is per-process, so Gunicorn's 4 workers multiplied the effective limit by 4 (and again per Cloud Run instance)
- **Teachers created in the UI could never sign in.** `create_teacher` never created the linked `auth.User`, so login was impossible *and* `/password-reset/` silently matched nobody. Now calls `ensure_user()`
- **The documented activation flow had never worked.** Django's `PasswordResetForm.get_users()` skips users with an unusable password — exactly what `seed_teachers` and the create-teacher screen produce. The page said "check your inbox" and sent nothing, for seeded teachers too. Added `ActivationFriendlyPasswordResetForm`; inactive users are still excluded
- Parent-portal magic-link login reused the pre-auth session id (session fixation) and left admin state in the same cookie; `parent_portal_logout` only popped `parent_id`. Both now flush
- The service worker cached `/login/` cache-first, serving a stale CSRF token after Django rotates the secret on sign-in. The Cache API ignores `Cache-Control`, so the server's `no-store` could not prevent it

**Billing correctness**

- **Quarterly payments ignored every discount.** `calculate_quarterly_amount` applied only the quarterly percentage, so a quarterly student with a sibling discount or a language cheque was billed full price — the enrollment row said one number and the generated payments said another. Measured: charging 153.90 where the enrollment said 86.21. Now mirrors `_apply_discounts`, and the previously-unused `quarter_due_month` parameter carries the June discount into Q3
- **Completed payments could report as zero income.** `update_payment` called `save()` without `full_clean()`, so `Payment.clean()`'s date backfill never ran; every income figure filters on `payment_date`, so a €54 payment showed as €0
- **Re-completing a payment rewrote financial history.** `quick_complete_payment` had no already-completed guard (the Stripe webhook did), so one stray click moved a payment between months in every report
- Payments attached to the *finished* enrollment for returning students — `enrollments.first()` is unordered and unfiltered
- `payment_type` / `payment_method` / `payment_status` were not validated against their choices; `payment_status="wat"` persisted and rendered raw
- `Enrollment.save()`'s `enrollment_amount` fallback was nested inside the `final_amount` branch, so supplying one without the other died on a NOT NULL violation

**"Esperado" &amp; reporting**

- Cancelled payments still counted as expected revenue, so cancelling one duplicate dragged the collection rate to 0 %. Added `LIVE_PAYMENT_STATUSES` as the single definition, applied in `payments_list`, the dashboard and `collection_rate`
- The payments summary mixed three timeframes in one line — "Esperado"/"Cobrado" were hard-wired to the current month while "Pendiente"/"Vencido" were all-time. It now describes the selected period and is labelled

**Crashes from ordinary input**

- `search_payments` and `export_payments` 500'd on any adult-student payment (`payment.parent.full_name`, no `None` guard) — one such payment broke the whole CSV export
- An unvalidated `save_schedule_slot` row permanently 500'd `/schedule/` for every user, with no UI to undo it. Now validated against the grid; the renderer skips out-of-grid rows
- `?offset=-1`, `?year=-1`, `?year=999999999999`, `?parent_id=abc`, over-long todos and over-long payment concepts all raised unhandled 500s
- `str(e)` reached the browser in 9 places, leaking `decimal.ConversionSyntax` and Postgres column widths — the leak class v1.14.4/v1.14.5 cleared 46 CodeQL alerts for
- reportlab parses a mini-HTML dialect, so a student called `O<Brien` raised `paraparser: syntax error` and killed PDF generation; `<b>x</b>` silently rendered as bold

**Emails**

- **No receipt was sent when a payment was recorded.** Only the Stripe webhook sent one, so cash and transfer payments marked complete in the UI sent nothing
- The adult monthly receipt queried parents of active children — it went to every child's parent and never to a single adult student
- The newsletter fell back to **every** parent when the selected group could not be found, while keeping that group's name in the subject
- Overlapping Fun Friday drains double-sent to every parent: `sent_at` was written *after* the batch. Now claimed with a conditional `UPDATE … WHERE sent_at IS NULL`
- `EnrollmentType.display_name` was seeded in English, so the Spanish matriculation email said "Monthly"/"Quarterly". Added `ENROLLMENT_TYPE_DISPLAY_ES`, plus an explicit **Forma de pago** line

**Data model &amp; admin**

- `AuditLog` was write-only: no admin, no view, no URL, no cap and no pruning, growing 16 rows per student per year. Registered read-only, plus `prune_audit_log` (weekly Beat + management-command wrapper)
- `SiteConfiguration.delete()` returned `None` instead of Django's `(count, dict)`, and `objects.all().delete()` bypassed the singleton guard entirely, wiping every price
- `update_site_config` skipped validators, so negative fees persisted and quietly broke every downstream calculation
- Registered `FunFridayScheduledSend` and `BacklogTask`; a queued mass-mail could not previously be inspected or cancelled
- Removed `gsheets` from `INSTALLED_APPS` — nothing imported it, and it was the source of the `makemigrations --check` drift. The dependency stays in `pyproject.toml` for planned future use

**Admin index (reported)**

- `/admin/billing/expense/` rendered a blank card titled `Gestión de .` — `templates/admin/index.html` used `{{ model.verbose_name_plural }}`, a key Django's `app_list` does not provide (it is `model.name`). Django renders missing variables as `''`, which is why it was silent. Expense was the only visibly broken card because every other model has a hardcoded branch. Added Spanish `verbose_name` to Expense, Payment, Enrollment and EnrollmentType

**Backlog delivered**

- **Waiting list** — a dedicated short form at `/students/waiting/create/` needing only a name and a phone number; `Student.birth_date` and `Student.group` are now nullable, with new `course`, `observations`, `waiting_contact_name` and `waiting_contact_phone` fields. Being moved to the waiting list was previously a **one-way door**: the enrollment stayed active (so billing continued) and the promotion then hit `unique_active_enrollment_per_student` and 500'd
- **Friday timetable** — four overlapping sessions (16:30–17:15 infantil, 16:00–17:25 primaria, 17:30–18:30 Fun Friday, 17:30–19:00 adultos) via a per-cell `FRIDAY_TIMES` map, no schema change
- **Students** — GDPR and allergy filters; a working per-row edit link. The old add/edit modal was dead in both directions (its form had no `action`, so creating POSTed to a `ListView` → 405; `editStudent()` called `.json()` on an HTML response) and nothing ever opened it — removed, ~370 lines
- **Payments** — cancel button, month/year filter, a per-student payment-history PDF, `quarterly` in the type dropdown (which had offered three types that were not valid choices)
- **Base de Datos** — group filter
- **Expenses** — recurring day extended to 31 ("último día del mes"); it was silently clamped to 28
- **Fun Friday** — default start time 17:30
- **QA** — backlog export to JSON/CSV
</details>

<details id="v1148">
<summary><strong>v1.14.8 — SameSite fix: Google OAuth login on Cloud Run</strong></summary>

The first real bug found by using production. Teacher email + password login worked, but every
Google OAuth attempt bounced straight back to `/login/` with **"Estado OAuth inválido"**.

**Root cause (`project/project/settings.py`)**

- `SESSION_COOKIE_SAMESITE` defaulted to `Strict` whenever `DEBUG=False`.
- The OAuth round trip ends with Google issuing a redirect to `/auth/google/callback/`. That return is a **cross-site top-level navigation** — the initiator is `accounts.google.com`, the destination is our domain — and `SameSite=Strict` instructs the browser to withhold the cookie on exactly that kind of request.
- Django therefore received no session cookie, built a fresh empty session, and found no `google_oauth_state` to compare against the `state` query parameter. The CSRF guard in `google_oauth_callback` (`core/views/auth.py`) did its job and rejected the callback.
- Email + password login was unaffected because it never leaves the site, so the cookie is never asked to survive a cross-site hop.

**Fix**

- `SESSION_COOKIE_SAMESITE` now defaults to `Lax` in every environment. `Lax` permits the cookie on top-level cross-site **GET** navigations — precisely the OAuth callback — while still withholding it on cross-site POSTs and subresource requests, which is where the CSRF risk actually lives. It is also Django's own default.
- `CSRF_COOKIE_SAMESITE` is deliberately left at `Strict`: the CSRF cookie is not needed on the callback GET, and every form POST is same-site.
- Side benefit: under `Strict`, arriving from any external link — a payment-reminder email, the CI deploy notification — rendered the teacher as logged out until they clicked something internal. That no longer happens.

**Docs**

- README's three SameSite tables said `Strict` in production; all now say `Lax` for the session cookie and carry the reason inline, so nobody "hardens" it back and silently breaks OAuth.
- The environment-variable reference splits `SESSION_COOKIE_SAMESITE` and `CSRF_COOKIE_SAMESITE` into separate rows — they no longer share a value.

</details>
<details id="v1147">
<summary><strong>v1.14.7 — Production Gunicorn fix + full documentation sync</strong></summary>

Production is **live** on Cloud Run as of this release:
[https://fiveaday-332600671945.europe-southwest1.run.app/login/](https://fiveaday-332600671945.europe-southwest1.run.app/login/).
Getting there needed a one-line container fix, and auditing the docs afterwards turned up a
large amount of drift that had accumulated across the v1.1-v1.14 feature work.

**Production boot fix (`Dockerfile`)**

- The image's default `CMD` ran `gunicorn project.wsgi:application` from `/app`, but `manage.py` lives at `/app/project/manage.py` and the Django settings package at `/app/project/project/` — so `project.wsgi` only resolves with `/app/project` as the working directory. On Cloud Run the container died at startup with `ModuleNotFoundError: No module named 'project.wsgi'`.
- Fixed by adding `--chdir project` to the `CMD`. This never reproduced locally: development uses `runserver`, and `docker-compose.testing.yml` overrides the command outright — so the only environment that ran the image's own `CMD` was production.
- Also added `--access-logfile -` and `--error-logfile -` so Gunicorn's request and error logs reach Cloud Logging via stdout/stderr instead of being swallowed.

**Documentation sync — the drift, itemised**

The README claimed counts and commands that stopped being true several releases ago:

- **Testing section** documented eight `make` targets that do not exist (`make test-unit`, `test-integration`, `test-local`, `test-sqlite`, `test-coverage`, `test-fast`, `test-k`). The Makefile has exactly two test targets: `make test` (with positional suite selector, `K=`, and `ARGS=`) and `make test-cov-gate`. `CLAUDE.md` carried the same three stale references.
- **Test tables** listed 22 of 46 unit files and 17 of 26 integration files — 33 test files were entirely undocumented, including every file for the waiting list, expenses, reports, parent portal, Stripe, PWA, 2FA, SMS, audit log, rate limiter and Google Sheets work. Both tables are now complete and their per-file counts sum to exactly the 1,061 tests pytest collects.
- **Per-app summary tables** contradicted the Directory Layout directly above them: `core` claimed 14 view modules (22), 13 JS modules (16) and 5 models (8, counting `AuditLog`); `billing` claimed 3 services (6), 4 models (5, missing `Expense`) and 20 URLs (23); `comms` claimed 6 Celery tasks (12) and omitted `SmsService`; `students` claimed 12 URLs (14) and omitted `ParentSessionToken`.
- **Counts corrected**: 1,008 -> 1,061 tests; 70 -> 72 test files; 15 -> 18 conftest fixtures; 4 -> 8 Celery Beat schedule entries; `~50` -> 12 email convenience functions; coverage table refreshed from a live run (28 files below 100%, 57 at 100%, 4,772 statements, 95.49%).
- **`core/schedule_utils.py` reached 100%** coverage in v1.14.5 and has been removed from the below-100% table, where it was still listed at 62%.
- **Project Status** showed production as "Pending" and the QA row's web address as "(will be provided once deployed on GCP)". Both now carry their real URLs.

**Docs**

- **CI was under-reporting coverage as 86.44%.** The test step runs with `working-directory: project`, but `[tool.coverage.run]` and its `omit` list live in the repo-root `pyproject.toml`, and coverage only reads config from the *current* directory. The omit list was silently ignored, so 42 files meant to be excluded — 22 migrations, 16 management commands, 4 `admin.py` — were counted: ~993 extra statements. `ci.yml` now passes `--cov-config=../pyproject.toml`, so CI, `make test` and `make test-cov-gate` all agree on 95.49%. This also stops the spurious `< 90%` warning that fired on every run and corrects the figure sent to Codecov.
- New `CLAUDE.md` gotcha recording the `--chdir project` requirement, so the next person to touch the `CMD` or add a container entrypoint does not reintroduce it.
- `CLAUDE.md`'s "12 view modules" and stale `make test-*` references corrected; per-app READMEs re-synced against their source.

</details>

<details id="v1146">
<summary><strong>v1.14.6 — SMS log-injection fix + shared comms log helper</strong></summary>

Closes the two Copilot review threads that were blocking the v1.14.5 release PR
(`main-protection` requires review-thread resolution).

**SMS log injection**

- `SmsService.send()` logged the destination number and the raw Twilio exception verbatim, and handed `str(e)` back in `SmsResult.error` — which callers surface in responses. The number originates from an admin-typed `Parent.phone` and the error text is remote input, so both are now passed through `safe_log()`, in the log record *and* in the returned result.
- 4 new tests: CR/LF stripped from the returned error, 200-char cap, the log record staying single-line for a forged phone number, and the existing message still readable.

**One log helper per app, not per module**

- New `comms/log_safe.py`. v1.14.5 had put a module-private `_safe_log` twin inside `email_service.py`; `sms_service.py` needing the same thing made that the second copy, so it is now one helper shared within `comms`. It stays a deliberate near-copy of `core/log_safe.py` rather than an import, because `comms` must not depend on `core`.
- Its docstring records that `safe_log()` makes code safe but does **not** clear CodeQL's `py/log-injection`, and points to coercion or omission as the stronger fix.

**Deferred, now tracked**

- Copilot also flagged `comms/tasks.py` importing `core.schedule_utils`, which reverses the documented dependency flow. It is pre-existing, there is a second identical violation at `comms/tasks.py:685` (`core.models.FunFridayScheduledSend`), and fixing only the flagged one would leave the codebase inconsistent with itself — so it is recorded as known debt in [CLAUDE.md](CLAUDE.md) (and the maintainer's local `docs/TODO.md`) as its own piece of work rather than rushed into a release. Both imports are lazy and function-body, so there is no import cycle today; the cost is coupling.

</details>

<details id="v1145">
<summary><strong>v1.14.5 — Log-injection remediation + CodeQL scoping</strong></summary>

Follow-up to v1.14.4. That release cut open CodeQL alerts from 46 to 16, but the
`safe_log()` sanitizer introduced there **did not** satisfy CodeQL's
`py/log-injection` query: the query treats `str.replace` as taint-preserving, so
stripping `CR`/`LF` makes the code genuinely safe without clearing the alert.
Worse, the `logger.exception(...)` calls added to fix stack-trace exposure
introduced seven *new* log-injection alerts of their own. This release closes
that out properly.

**Log injection — coerce instead of sanitize (9 alerts)**

- Every id logged in an error path arrives through an `<int:...>` URL converter, so `logger.exception("... %d", int(payment_id))` is a runtime no-op that breaks the taint outright — far stronger than scrubbing a string. Applied in `payments.py` (5 sites), `stripe_views.py`, `fun_friday_attendance.py` (2 sites) and `waiting_list.py`.
- `safe_log()` and `core/log_safe.py` are retained: still the right tool for values that genuinely are free-form text.

**Log injection — stop logging the value (4 alerts)**

- `rate_limit._client_ip()` now parses `X-Forwarded-For` through `ipaddress` and falls back to `"unknown"`. The header is client-supplied and fed **both** a cache key and a log record, so a malformed value could pollute the rate-limit key space as well as the log; addresses are also normalised so one client can't occupy several buckets by varying the textual form.
- The parent portal no longer logs the address on an unregistered-email login attempt. That endpoint exists specifically to not reveal whether an email is registered, and the log was leaking exactly that.
- `EmailService` logs `template_name` (developer-controlled) instead of `subject` (built from user input by some callers) — also more useful for ops, since it names the email.

**Stack-trace exposure (1 alert)**

- `update_payment`'s combined `except (InvalidOperation, ValidationError)` is split. `InvalidOperation` was returning Decimal's internal `[<class 'decimal.ConversionSyntax'>]` repr to the browser; it now returns "El importe introducido no es válido." `ValidationError` keeps `e.messages`, which is Django's written-for-humans validation text.

**CodeQL scoping (2 alerts)**

- Inline `# codeql[query-id]` suppression comments are **not honoured** by this setup, so the two added in v1.14.4 were removed rather than left implying a handled alert.
- New `.github/codeql/codeql-config.yml` moves the query suite and adds `paths-ignore` for `scripts/` (operator utilities, never shipped in the image, never in a request path — `generate_secure_password.py` prints a secret by design) and `project/project/settings_test.py` (the Django settings star-import can't be enumerated).

**Tests — 1,058 passing, 95.49 % coverage**

- `test_schedule_utils.py` (27 tests) takes `core/schedule_utils.py` from 62 % to full coverage: row/day band mapping, the Friday override, duplicate-column collapsing, day ordering, out-of-range days, and group isolation. It feeds both the schedule view and the welcome email, so a regression there misinforms parents.
- `test_rate_limit.py` gains IPv4/IPv6 normalisation, malformed-header rejection (including CR/LF payloads) and the empty-header fallback.
- Writing the payment test surfaced a **latent bug in the existing suite**: `test_json_invalid_amount_returns_400` was passing on a 400 from the *parent-association* check and never reached `Decimal()`, so the amount-parsing branch was untested. The new test posts a linked student/parent pair to actually exercise it.

</details>

<details id="v1144">
<summary><strong>v1.14.4 — Code-scanning cleanup + CVE dependency bumps</strong></summary>

Clears every open CodeQL alert on the branch and the three red checks on PR #36
(Lint / Dependency review / Trivy). No user-visible behaviour changes beyond
AJAX error messages, which are now generic instead of echoing Python exceptions.

**Stack-trace exposure (22 sites, medium)**

- AJAX endpoints across `payments`, `management`, `testing_tools`, `students`, `schedule`, `support`, `todos`, `fun_friday_attendance`, `waiting_list` and `stripe_views` returned `str(e)` in their JSON error payload, leaking exception text (and, for DB/integrity errors, table and column names) to the browser.
- Each catch-all now logs the full traceback server-side with `logger.exception(...)` and returns a fixed Spanish message. Genuine validation errors (`ValidationError`, `InvalidOperation`) still surface their own user-facing text — only the catch-alls were changed.
- Eight view modules gained a module-level `logger`.

**Log injection (7 sites, medium)**

- New `core/log_safe.py` with `safe_log()` — strips `CR`/`LF`/`VT`/`FF`/`ESC` and caps length at 200 chars, so an attacker-supplied value can't forge extra log records or smuggle terminal escapes into a tailed log.
- Applied to the client IP in `rate_limit`, the submitted email in the parent portal, the path-supplied payment id in `payments` + `stripe_views`, and the OAuth `authorization_response` in `auth`.
- `comms/services/email_service.py` carries a module-private twin (`_safe_log`) rather than importing from `core`, keeping the documented app dependency direction intact. Its two f-string log calls also became lazy `%s` calls.
- 11 unit tests in `tests/unit/test_log_safe.py`.

**Sensitive data in logs (2 sites, high)**

- The OAuth state-mismatch warning logged both state values verbatim; it now logs only `session_state_present` / `param_state_present` booleans. The state is a CSRF token, and the query-string side is attacker-controlled — this one line was both a `clear-text-logging` and a `log-injection` hit.
- `scripts/generate_secure_password.py` keeps printing the generated secret (that is the tool's entire purpose) with an explanatory comment and a `codeql[...]` suppression.

**Note-level alerts (15)**

- Four bare `except: pass` blocks (`middleware`, `waiting_list`, `app_forms` ×2) documented with why swallowing is correct.
- Dead `logger` globals removed from `billing/services/pdf_service.py` and `core/audit_signals.py`, along with their now-unused `logging` imports.
- `core/models.py` and `students/models.py` declare `__all__`, so the `AuditLog` / `ParentSessionToken` sibling-module re-exports read as intentional instead of unused imports.
- `CELERY_TASK_ALWAYS_EAGER` / `CELERY_TASK_EAGER_PROPAGATES` are now assigned unconditionally (`= not CELERY_BROKER_URL`) rather than inside an `if`.
- Five `lambda *args, **kw: date(*args, **kw)` mock side-effects in `test_context_processors.py` collapsed to plain `date`.
- The intentional settings star-import in `settings_test.py` documented + suppressed.

**Copilot review comments**

- `django` was the only unbounded dependency — now `>=6.0.8,<7`, so a Django 7 can never land unreviewed while the 6.x line stays open.
- Lock moved to **Django 6.1**; full suite verified green on it. 6.1 deprecates the whole `EMAIL_*` settings family in favour of `MAILERS`, and `EmailMessage.send(fail_silently=...)` — 56 `RemovedInDjango70Warning`s now surface in the test run. Nothing breaks before 7.0, and the `<7` bound is what keeps that migration a deliberate, scheduled piece of work rather than a surprise.
- `sheets.py`'s docstring advertised a `?target=` query param on a POST-only endpoint that reads the form body; corrected.
- `students/migrations/0003_teacher_user.py` used `models.deletion.SET_NULL`. That resolves fine at runtime (importing `django.db.models` registers the `deletion` submodule), so the migration was never broken — but it now uses the explicit `django.db.models.deletion` path every other migration in the repo uses.

**CVE dependency bumps (Lint / Dependency review / Trivy)**

- `cryptography` 49.0.0 → **50.0.0** — GHSA-g6cj-pr64-35w5, PKCS#7 `EnvelopedData` Bleichenbacher oracle (high). This was the alert failing Dependency review.
- `django` 6.0.7 → **6.1** — clears PYSEC-2026-3717 (fixed in 6.0.8).
- `sqlparse` 0.5.5 → **0.6.0** — PYSEC-2026-3696/3697/3698/3699.
- `pip` 26.1.2 → **26.2.1** — PYSEC-2026-3721.
- `reportlab` gained a `<6` bound. `uv run pip-audit` now reports no known vulnerabilities.

Suite at **1,019 tests, 95 % coverage**.

</details>

<details id="v1143">
<summary><strong>v1.14.3 — Dependency bumps + main-branch history reconciliation</strong></summary>

**Dependency updates (Dependabot)**

- `dawidd6/action-send-mail` v17 → **v18** in the deploy-notification workflows (#32)
- `ossf/scorecard-action` 2.4.0 → **2.4.4** in the Scorecard supply-chain workflow (#33)
- `django-filter` constraint relaxed from `>=25.1,<26` to `>=25.1,<27` (#31)

**Branch-history reconciliation**

- `main` had accumulated squash-merge commits (up to v1.0.10) that were not ancestors of `testing`/`development`, so the `testing` → `main` release PR reported merge conflicts (`base.html`, `settings.py`, `pyproject.toml`, `uv.lock`, admin templates, favicons). `main` was merged into `development` with the `ours` strategy — a content-verified no-op (main's tree was byte-identical to development's own v1.0.10 commit) that records `main` as an ancestor, so future `testing` → `main` PRs merge cleanly.

</details>

<details id="v1142">
<summary><strong>v1.14.2 — Beat-task command wrappers + persisted Fun Friday sends</strong></summary>

**Production-readiness: periodic tasks without Celery Beat**

- Every Celery Beat task now has a thin **management-command wrapper** that runs it synchronously via `.apply()`, so Cloud Scheduler → Cloud Run Jobs (or plain cron) can trigger them in production, where no Beat process exists: `send_birthday_emails`, `send_payment_reminders`, `send_monthly_report` (`--recipient`), `materialize_recurring_expenses` (`--daily`, `--month/--year`, `--date`), and `cleanup_backlog_tasks` (`--days`). `DEPLOYMENT.md` gains the full command ↔ cron schedule table for the Cloud Scheduler setup.

**Fun Friday sends survive eager mode**

- Fun Friday announcements were queued with `apply_async(eta=Monday 14:30)` — under `CELERY_TASK_ALWAYS_EAGER=True` (production has no Celery worker) the ETA is silently ignored and the email went out **immediately**. The form now persists a **`FunFridayScheduledSend`** row (new `core` model, migration `core/0005`) and the new `send_due_fun_friday_emails_task` drains due rows idempotently (marks `sent_at`, never re-sends) — via Celery Beat daily at 14:30 in dev/testing and the `send_due_fun_friday_emails` command in production. Announcements created after their Monday slot drain immediately.

**Testing**

- 23 new tests: the six command wrappers, the `FunFridayScheduledSend` model + drain task, the new Beat-schedule entry, and the form's persist / immediate-drain paths. Suite at **1,008 tests, 95% coverage**.

</details>

<details id="v1141">
<summary><strong>v1.14.1 — Email restyle + dark-mode emails</strong></summary>

**Transactional email overhaul**

- All 17 transactional email templates (enrollment child/adult, payment receipt, receipts for enrollment/quarterly/adult, payment reminders, Fun Friday, birthday, vacation closure, tax certificate, monthly + admin reports, newsletter, parent magic link, password reset) were **restyled to match the `welcome_student` reference** — consistent violet headings, rounded info cards, coloured callouts and table dividers — while preserving every template variable and the shared signature/legal footer.
- `welcome_student.html` was aligned to the app's violet palette (`#6d28d9`) and gained a **WhatsApp CTA** (`wa.me/34613481141`, 613 481 141) inside its "¿Tienes alguna pregunta?" box.

**Dark-mode emails**

- `base_email.html` now ships an inline `@media (prefers-color-scheme: dark)` stylesheet (plus a `color-scheme` meta) so emails render in a dark violet theme that mirrors the webapp — targeting the inline hex values with attribute selectors, the same technique `theme.css` uses for the app. The signature/footer **content** is unchanged; only its dark rendering was added.

</details>

<details id="v1140">
<summary><strong>v1.14.0 — Comprehensive in-app help guides</strong></summary>

**In-app help**

- Every main view (Home, Students, Waiting list, Schedule, Payments, Expenses, Apps, Management, Reports, Database) **and** the Testing panel now has a genuinely thorough Spanish guide behind its bottom-left "?" button — each walks through every section, button, filter and the typical workflow, with role differences and tips (the modal scrolls). Home's guide also documents the keyboard shortcuts.
- The Testing guide explains how to **simulate a non-admin teacher** by logging in as the seeded `test@test.com` account (the password is not printed — the repo is public).

**Dark theme**

- Fixed the confirmation modal's **Cancelar** button, which was barely legible in dark mode (now uses the themed `primary` utilities instead of an inline dark-violet colour).

This release caps the rapid v1.13.x iteration (dark theme, testing-dashboard redesign, admin-only QA, richer seeder, non-admin UX, recurring-expense frequencies, CI deploy emails, keyboard nav) with a complete self-service help layer.

</details>

<details id="v11311">
<summary><strong>v1.13.11 — Keyboard nav hotkeys + per-view help panels</strong></summary>

**Keyboard quick-nav**

- Number keys jump between sections (only outside text fields): <kbd>0</kbd> Home, <kbd>1</kbd> Students, <kbd>2</kbd> Waiting list, <kbd>3</kbd> Schedule, <kbd>4</kbd> Payments, <kbd>5</kbd> Expenses, <kbd>6</kbd> Apps, <kbd>7</kbd> Management, <kbd>8</kbd> Reports, <kbd>9</kbd> Database. Implemented via `data-hotkey` on the sidebar links (so hidden admin-only links are inert for non-admins). Small number badges are shown on each sidebar icon (CSS `::after`).

**Per-view help**

- Every main view (+ `/testing/`) has a small **"?"** button in the bottom-left corner opening a modal that explains the view's features in plain language; Home's help also lists the keyboard shortcuts. Content lives in each template's `{% block help_content %}`; the button only appears when the page provides help.

**Docs**

- Added the new CI deploy-email secrets (`TESTING_NOTIFY_EMAILS`, `TESTING_URL`, `SUPPORT_EMAIL`, `PRODUCTION_URL`) to the README's Required GitHub Secrets table.

</details>

<details id="v11310">
<summary><strong>v1.13.10 — CI deploy emails, recurring-expense frequencies, backlog screenshots</strong></summary>

**CI deploy notifications**

- The **development → testing** auto-merge now emails support + the two admin teachers a friendly, readable notice: what changed, a prominent **"Open testing environment"** button (the testing URL), and the technical details (old→new version, tags, merge commit) at the end. Recipients come from the `TESTING_NOTIFY_EMAILS` secret (falls back to `OWNER_EMAILS`); URL from `TESTING_URL`.
- The **production** (`main`) notification now also goes to `SUPPORT_EMAIL` (alongside `hellofiveaday@gmail.com`), with the same readable format (old→new version, optional `PRODUCTION_URL` button, deploy steps).

**Recurring expenses**

- Recurring expenses now support **monthly** (day-of-month), **yearly** (day + month) and **weekly** (any subset of weekdays — each Monday, Monday+Tuesday, … or every day). New `recurring_frequency` / `recurring_month` / `recurring_weekdays` fields (migration `billing/0006`); weekly/yearly materialise via a new daily Celery-beat task (`materialize_recurring_expenses_daily_task`, idempotent). The expenses form gained the frequency selector + weekday checkboxes.

**Testing dashboard**

- The **¿Listo para desplegar?** check now opens a styled **confirmation modal** before emailing.
- Backlog tickets can include a **screenshot** — it is **attached to the notification email and never stored** (max 5 MB, images only) to keep storage in check.
- In the testing environment, the help modal shows a banner pointing testers to the dedicated **Testing panel** (with a direct link); the help form is meant for production.

</details>

<details id="v1139">
<summary><strong>v1.13.9 — Non-admin teacher UX, teacher-admin lock, backlog housekeeping</strong></summary>

**Non-admin teachers**

- Home hides all financial widgets from non-admin teachers (*Pagos pendientes*, *Ingresos del mes*, the pending-payments modal, and the *Nuevo Pago* button) via `{% if is_admin_user %}`.
- Non-admin teachers can now **view** the schedule (`schedule_view` added to the middleware whitelist); the edit toggle is hidden for them and `save_schedule_slot` stays admin-only, so the schedule is read-only for non-admins.

**Teacher admin lock**

- Teachers created from the management page are **always non-admin** (`create_teacher` forces `admin=False`; the "Administrador" checkbox is removed). Only the seeded teachers (`TEACHER_SEED_*`) and the superuser are admins; an admin promotes others via `/admin/`.

**Backlog housekeeping**

- Marking a QA backlog task **done** emails the admin teachers a summary. Done tasks are **auto-deleted after 30 days** by a new daily Celery-beat task (`core.tasks.cleanup_done_backlog_tasks`).

**UI**

- Login page title now animates to a legible lavender in dark mode (was near-black). Sidebar nav icons are vertically **centered** instead of pinned to the bottom.

</details>

<details id="v1138">
<summary><strong>v1.13.8 — Dark theme, Testing redesign, QA admin-only, richer seeder</strong></summary>

**Theme (light + dark)**

- Real **dark theme** delivered as `html.dark` overrides in `core/static/css/theme.css` (no `dark:` variants — the app uses hard-coded utility classes); violet-tinted dark surfaces that complement the light violet palette, plus dark status badges, pagination, schedule grid, apps/testing cards, and inline `style="background:#fff"` cards caught via attribute selectors.
- **Time-based default**: light 10:00–16:59, dark otherwise, when the user hasn't explicitly toggled. An explicit choice is kept only during the session; logout or **6h inactivity** expiry (session now `21600s` + `SESSION_SAVE_EVERY_REQUEST`) lands on `/login/`, which clears the saved theme back to the time-based default. Toggle available in the header and on the login page.
- Schedule group names lighten for contrast on dark and re-render live on toggle.

**Testing dashboard redesign**

- New **"¿Listo para desplegar?"** card → emails `SUPPORT_EMAIL` a full version snapshot (version, environment, last commit, Python/Django, DB, datetime + who marked it). New `api_mark_ready` endpoint.
- Right column reorganised: Reporte de errores · **[GitHub docs] + [Correo temporal]** (tempmail.lol) · **[Admin] [Drive] [GCP]** big icons. GitHub docs code-icon added to the Proyecto card; the last-commit message now wraps instead of clipping. Backlog form: smaller title, larger description, tiny primary-coloured create button.
- **QA access is now ADMIN-only** — `qa_access_required` + `show_testing_tools` require `teacher.admin`; non-admin teachers get a 404 and no sidebar icon. QA URLs removed from the non-admin whitelist. `git` added to the Docker image (+ `safe.directory`) so the "last commit" card populates.

**Payments / UI**

- Service worker reverted to **cache-first** (optimal for content-hashed immutable assets). `NoHtmlCacheMiddleware` marks dynamic HTML `no-cache` so asset hashes stay fresh after deploys. Pagination (payments + database), the "Volver" buttons, expenses "Consultar recibos" button, and sidebar hover/active states restyled to fit both themes. Header icons made perfectly round. Reports gained an icon/title/explanation header.

**Seeder**

- `seed_testdata` rewritten for a coherent QA dataset — 20 active students (3 adults + siblings) + 1 inactive, 15 parents, 4 command teachers (2 admins reused + 2 new), 8 groups, monthly/quarterly enrollments with sibling-discount / language-cheque / returning-student, payments in **every** status with amounts derived from the pricing services, and realistic small **expenses** so Reports & Expenses render coherent numbers.

</details>

<details id="v1137">
<summary><strong>v1.13.7 — Service worker network-first (stale-style fix)</strong></summary>

**PWA / caching bug fix**

- The service worker cached `/static/` assets **cache-first**, so after the first load it served **stale CSS/JS** on normal navigation — the theme looked wrong until a hard refresh, then broke again when changing views. Switched the SW to **network-first** for cacheable assets (static, media, manifest, login): the freshest CSS/JS/theme always wins when online, with the cache kept only as an offline fallback. Also set `sw.js` to `no-cache` so a new worker is picked up on the next navigation. Bumping the app version rotates `CACHE_NAME`, purging the old cache on activate.

</details>

<details id="v1136">
<summary><strong>v1.13.6 — Single violet theme + Fun Friday email scheduling</strong></summary>

**Theme**

- Reverted the v1.13.5 pink experiment: the app uses the **original violet palette** again (primary-500 `#8b5cf6`), and it is now the **same for both light and dark** (the beloved classic look). The light/dark toggle stays in the header, but `theme.css` currently only swaps the toggle icon — `html.dark` makes no visual change. A dedicated dark theme will be added later as `html.dark` overrides in `theme.css`.

**Fun Friday**

- The Fun Friday announcement is no longer sent immediately. It's now **scheduled** (Celery `apply_async(eta=…)`) for **14:30 on the Monday of the target Friday's week** — e.g. a Fun Friday on the 17th queues the emails for Monday the 13th at 14:30. New `send_fun_friday_emails_task` in `comms/tasks.py`; the QA "test send" path still sends immediately.

</details>

<details id="v1135">
<summary><strong>v1.13.5 — Adult payments + welcome email (theme experiment)</strong></summary>

**Theme**

- Restored the academy's original **rose/pink** palette (primary-500 `#f93a76`) as the **light** theme (the default look before the switch to violet). The **dark** theme keeps the violet look: `theme.css` fully re-skins every `primary` utility to violet under `html.dark`, so no pink leaks into dark mode. `theme-color` meta updated to the pink.

**Payments**

- Adult students have no parent/guardian, which is valid. `create_payment` now requires a parent only for non-adult students and creates the payment with `parent=None` for adults; the create-payment JS mirrors this (no parent requirement in the submit guard for adults).

**Welcome email**

- The enrolment email now shows the student's **exact class schedule** derived from their group's slots (e.g. "Viernes de 16:10 a 17:30"), via the new shared `core/schedule_utils.py` (single source of truth for the row/day → time mapping, also used by the schedule view).
- Reworded the welcome message to the academy's new copy.

</details>

<details id="v1134">
<summary><strong>v1.13.4 — Dark theme + testing-stack bug sweep</strong></summary>

**Theme (light / dark)**

- Added a persistent light/dark theme toggle in the header, next to the notifications bell. Light is the untouched default; dark is a violet-tinted theme in tune with the `primary` palette. The choice is saved in `localStorage` and applied before first paint (no flash). Implemented with `darkMode: 'class'`, a new `core/static/css/theme.css` override sheet (inert unless `html.dark`), and `core/static/js/theme.js`.

**Bug fixes (only manifested in the testing/production stack, `DEBUG=False`)**

- **Systemic CSRF failure** — the `csrftoken` cookie is `HttpOnly` when `DEBUG=False`, so JS `getCsrf()` helpers that read `document.cookie` returned an empty token and every AJAX POST 403'd. Fixed the helpers in `home.js`, `students.js`, `payments.js`, `schedule.js`, `student-detail.js`, and `fun-friday.js` to read the hidden `{% csrf_token %}` input first (cookie fallback). This repaired **completing a payment**, **completing a todo** (completed todos now disappear), **Fun Friday enrollment** (dedicated view + the icon in other views), and schedule saves — all in one fix.
- **Create-payment student search** — `search_students` rendered the full students HTML page instead of JSON, so the student autocomplete never populated and the create form stayed blocked. It now returns `{"results": [{id, full_name, school}]}`; selecting a student auto-fills the parent. Also removed a stray `ReferenceError` (undefined `parentSearch`) on the create page.

**Payments scheduling**

- Enrolling a student now schedules the whole academic year of pending fees, not just the enrollment fee: `PaymentService.schedule_academic_year_payments()` creates monthly (Sep–Jun) or quarterly (Oct/Jan/Apr) pending payments due at period end, starting at the enrollment month. It's idempotent, so the periodic `generate_payments` command never double-creates. Wired into both student creation and waiting-list assignment.

**QA testing tools access**

- Removed the dedicated `manitas` QA user and the `QA_TESTING_USERNAME` setting. The `/testing/` dashboard and its sidebar icon are now gated on **any logged-in Teacher** in the testing environment (`core.decorators._request_teacher`), including non-admin teachers (their whitelist now covers `testing_tools` + the QA API endpoints).

</details>

<details id="v1133">
<summary><strong>v1.13.3 — pip-audit CVE fixes + dependabot action bumps</strong></summary>

**Security / dependencies**

- Bumped `msgpack` 1.1.2 → 1.2.1 (GHSA-6v7p-g79w-8964) — transitive via `pip-audit[filecache] → cachecontrol`. This was the reported CI `pip-audit` failure.
- Bumped `Django` 6.0.6 → 6.0.7 (PYSEC-2026-2090 / 2091 / 2092), surfaced by `pip-audit` once msgpack was patched. `uv run pip-audit` now reports no known vulnerabilities.

**Dependabot (resolved as one commit)**

- `actions/checkout` v4 → v7, `codecov/codecov-action` v5 → v7, `docker/build-push-action` v6 → v7, `actions/dependency-review-action` v4 → v5, `dawidd6/action-send-mail` v3 → v17, applied across all `.github/workflows/*.yml`.

</details>

<details id="v1132">
<summary><strong>v1.13.2 — Vacation-closure email cross-month fix</strong></summary>

**Bug fix**

- `vacation_closure.html` and `send_vacation_closure_email` already supported `month_closure_end` (the month of the closure's END date), but `vacation_closure_form` never derived or passed it — so a closure spanning two months (e.g. Navidad, 23 Dec → 3 Jan) rendered "hasta el 3 de **diciembre**" instead of enero.
- The view now derives `month_closure_end` from the closure end date on all three paths (preview, real send, and the default GET preview). Added a view-level regression test (the template level was already covered).

</details>

<details id="v1131">
<summary><strong>v1.13.1 — Inline-image emails fixed for Django 6.0</strong></summary>

**Bug fix**

- `EmailService.send_email` set the `EmailMessage.mixed_subtype` attribute, which Django 6.0 removed — raising `AttributeError` for any email carrying an inline image. This crashed Fun Friday emails sent with an event image and the `test_all_emails` QA command.
- Inline images are now attached as a modern `email.message.MIMEPart` with a `Content-ID` header and `Content-Disposition: inline`, so `<img src="cid:…">` references resolve without the removed attribute.
- `test_all_emails` now sets the `event_image` flag alongside its inline attachment, so the Fun Friday preview renders the image instead of orphaning it.
- Added a regression test that sends a real inline image and asserts the `Content-ID` / inline part is present.

</details>

<details id="v1130">
<summary><strong>v1.13.0 — Admin TOTP 2FA + Returning-Student Discount + Tech-Debt Sweep</strong></summary>

**Admin two-factor authentication (TOTP)**

- New `Teacher.two_factor_secret` / `two_factor_enabled` / `two_factor_backup_codes` fields (base32 secret, boolean flag, JSON list of sha256-hashed one-time backup codes).
- New `core/services/two_factor_service.py` wraps `pyotp` for TOTP generation + verification (30-second window, `valid_window=1` slack) and `qrcode` for the enrolment QR code. Backup codes are generated in plaintext, shown to the user exactly once, and persisted as sha256 hashes.
- New views: `/two-factor/setup/` (renders QR + backup codes, POST to confirm enrolment), `/two-factor/manage/` (rotate backup codes, disable), `/two-factor/verify/` (mid-login gate, rate-limited to 6/min/IP against brute force).
- Login flow: password check succeeds → if `Teacher.two_factor_enabled` the request is redirected to `/two-factor/verify/` with a short-lived pending session (`_2fa_pending_user_id`, 5-minute expiry) that is NOT yet marked `is_authenticated`. Only after the OTP or backup code verifies does `_finalize_session_login` promote the session. Google OAuth logins take the same gate — a scanned OAuth email is only one factor.
- New `manage.py reset_two_factor <email>` command wipes the secret + codes for a locked-out admin (recovery flow when both phone and all backup codes are lost).
- Only Teachers with `admin=True` can reach setup/manage — non-admins are bounced back to `home` with a flash message. `two_factor_verify` is in `SimpleAuthMiddleware.PUBLIC_PREFIXES` since it must be reachable before the session is fully authenticated.
- Enrolment package: 8 backup codes (8-hex-char) generated per user, single-use.
- 34 tests: TOTP + backup-code semantics, enrolment happy path, wrong-code rejection, rate-limited verify, admin-only gating, `reset_two_factor` management command.

**Returning-student enrollment discount**

- New `SiteConfiguration.returning_student_enrollment_discount` (Decimal, default €20.00) exposed as an editable field in the Management → Discounts panel.
- New `EnrollmentService.is_returning_student(student, this_academic_year)` — a student is "returning" iff they have any prior `Enrollment` for a different academic year (any status: active, finished, cancelled — all count, they were once signed up).
- New `EnrollmentService.compute_enrollment_fee(config, student, is_adult)` — returns `(final_fee, discount_applied)` with the returning-student discount subtracted (floored at 0). Adults are always excluded from this discount (they have their own separate `adult_enrollment_fee`).
- The discount is applied automatically in both enrollment-fee creation paths — `StudentCreateView.form_valid` (new-student flow) and `waiting_list_view.assign_from_waiting_list` (waiting-list promotion). The concept string on the resulting `Payment` includes `"(dto. alumno recurrente −20.00 €)"` when applied, so the admin can see where the discount came from.
- **Stacks with sibling + language-cheque discounts** (each targets a different fee — sibling/cheque hit the monthly fee, returning-student hits the one-time enrollment fee).
- 11 tests covering the detection helper, the fee-compute helper (with and without discount, adult exclusion, zero-configured no-op, floor-at-zero on huge values), the `SiteConfiguration` default, and the management update API.

**Tech-debt sweep**

- Created `student_update.html` — the class-based `StudentUpdateView` had `template_name = "student_update.html"` but no file existed, so a real `GET /students/<id>/update/` would 500. The new template renders both the student form and the enrollment form, with an amber notice at the top when the student is on the waiting list.
- Fixed the two pre-existing SQLite ordering flakes in `test_transactions.py`: `Payment.objects.order_by("-created_at")` was non-deterministic on SQLite (millisecond-precision timestamps meant tie-broken order was arbitrary). Added `-id` as a stable secondary key in both `get_payments_for_last_two_school_years` and `get_all_payments_unrestricted`. The tests now pass on both SQLite and PostgreSQL — no more `--deselect` in the CI command.

</details>

<details id="v1120">
<summary><strong>v1.12.0 — Installable PWA</strong></summary>

- New `/manifest.webmanifest` endpoint serves the web app manifest (name, icons, theme colour, three home-screen shortcuts). Enables "Add to Home Screen" on iOS + Android and installable-app prompts on desktop Chromium.
- New `/sw.js` endpoint serves a purpose-built service worker: cache-first for same-origin GETs to the dashboard shell (`/`, `/students/`, `/payments/`, the logo), network-first for everything else. Never caches `/api/*`, `/login/`, or `/logout/` — those must always be fresh.
- Cache key is derived from `APP_VERSION`, so every `make version` bump invalidates the client cache automatically on the next visit.
- Base template picks up the manifest link, viewport-appropriate meta tags (`theme-color`, iOS + Android web-app-capable, custom status-bar style), and a small idempotent registration script that runs `navigator.serviceWorker.register("/sw.js")` after `window.load` so the initial paint isn't blocked.
- Both endpoints added to `SimpleAuthMiddleware.PUBLIC_PREFIXES` — installability probes and offline reloads must succeed without a session cookie.
- 8 new tests covering the manifest shape, cache-control headers, `Service-Worker-Allowed: /`, cache-key rotation on version bump, and unauthenticated accessibility.

</details>

<details id="v1110">
<summary><strong>v1.11.0 — Stripe Payment Integration</strong></summary>

- New `billing/services/stripe_service.py` — direct httpx calls to Stripe's Checkout + webhook APIs. No `stripe` SDK dependency; the two endpoints we use don't justify the install-image weight. Dormant until `STRIPE_SECRET_KEY` is set (`is_configured()` gates the frontend button).
- New `Payment.stripe_session_id` + `Payment.stripe_payment_intent` fields, both indexed so the webhook can look up the target payment in constant time.
- Two new endpoints: `POST /parent/payments/<id>/pay-online/` (parent-portal-only, creates a Checkout session and returns the URL for the client to redirect to) and `POST /api/stripe/webhook/` (CSRF-exempt, signature-verified, added to `PUBLIC_PREFIXES` so the admin middleware doesn't block Stripe's callers).
- Webhook handler reconciles two events: `checkout.session.completed` marks the payment as completed and stores the PaymentIntent id; `checkout.session.expired` wipes the session id so a new link can be issued.
- HMAC-SHA256 signature verification with a 5-minute tolerance window. Verification is skipped only when `STRIPE_WEBHOOK_SECRET` is unset — production must set it.
- Parent portal payments table gains a "Pagar online" button on every pending row; a tiny JS shim posts to the endpoint and redirects the browser to Stripe.
- Three new settings (`STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`), all env-driven, all default to empty.
- 22 new tests covering the service happy path, StripeError propagation, HMAC verification (valid / tampered payload / expired timestamp / missing secret bypass), webhook reconciliation for the four event branches, and the endpoint surface (401 / 404 / 409 / 503 / 200).

</details>

<details id="v1100">
<summary><strong>v1.10.0 — Audit Log & Security Hardening</strong></summary>

- New `AuditLog` model (`core/audit_models.py`) — immutable trail of who changed what and when. Distinct from `HistoryLog` (compact 1,000-entry user feed): audit rows are machine-readable, retained forever, and record per-field diffs as JSON.
- `AuditActorMiddleware` stashes the current authenticated user into a `contextvars.ContextVar` (WSGI-local + ASGI-safe) so signal receivers attribute changes without threading the user through every save.
- `pre_save` snapshots the DB row before update; `post_save` diffs the snapshot against the new state and records only the changed fields. `post_delete` records the deletion with the last known label. Tracked models: Student, Parent, Teacher, Group, Enrollment, Payment, SiteConfiguration, Expense.
- New `core.rate_limit.rate_limit(scope, limit, window_seconds)` decorator — cache-backed IP throttle (Django's local-memory cache by default; swap to Redis via CACHES for multi-instance Cloud Run). Applied to admin login (5/min/IP) and parent-portal login (5/min/IP). Only counts POST so normal page loads never trigger.
- Rate limiter respects a `RATELIMIT_ENABLE` settings flag; `settings_test.py` sets it to `False` so cache state doesn't leak across tests.
- 10 new tests: audit-signal create/update/delete/diff coverage + rate-limit allow/block/GET-bypass/per-IP-isolation.

</details>

<details id="v190">
<summary><strong>v1.9.0 — Parent Portal</strong></summary>

- New read-only web portal for parents at `/parent/`. Completely separate from the admin auth surface: its own session key, its own base template, its own template folder.
- Magic-link authentication (30-minute TTL): POST `/parent/login/` with an email → the system issues a `ParentSessionToken` (via `secrets.token_hex(16)` for 128 bits of entropy) and emails a link to `/parent/login/<token>/`. Enumeration protection: unknown emails also see the "check your inbox" page. Tokens are single-use — `consume()` marks `used_at` and refuses reuse.
- Portal surface: `parent_portal_dashboard` (children, upcoming payments, downloads), `parent_portal_payments` (filterable by year), `parent_portal_receipt` (PDF, scoped to the current parent by 404), `parent_portal_tax_certificate` (PDF for the given year), and `parent_portal_logout`.
- New `students.ParentSessionToken` model in a sibling module (`parent_portal_models`) to keep `students.models` focused. Imported through the app so migrations pick it up.
- `SimpleAuthMiddleware.PUBLIC_PREFIXES` gains `/parent/` — the admin session middleware doesn't get in the way of a parent's own session.
- 22 tests split across `tests/unit/test_parent_session_token.py` (token issue/validity/consume) and `tests/integration/test_parent_portal.py` (magic-link flow, portal pages, cross-parent access denial, receipt PDF signature).

</details>

<details id="v180">
<summary><strong>v1.8.0 — SMS Notifications (Twilio, opt-in)</strong></summary>

- New `Parent.sms_opt_in` field (BooleanField, default False). Concrete opt-in per parent — SMS is never sent without an explicit True.
- New `comms.services.sms_service.SmsService` wraps the Twilio SDK behind an `is_configured()` guard + `SmsResult` dataclass. `twilio` is imported lazily so environments that don't use SMS never pay the install cost.
- New Celery task `comms.tasks.send_payment_reminder_sms_task` — retries on failure, gracefully skips when the service is unconfigured, and returns a structured `SmsResult` dict on success.
- Existing `send_payment_reminders` now supplements email with SMS for every opted-in parent — email remains the primary channel; SMS is a nudge on top. A Twilio outage cannot stall email because the SMS branch queues asynchronously.
- Three new settings (`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`), all optional and read from env vars.
- 13 tests covering configuration detection, low-level send, opt-in guard, phone-missing guard, and the Celery task's four branches.

</details>

<details id="v170">
<summary><strong>v1.7.0 — Reports & Analytics</strong></summary>

- New `core.services.analytics_service` with `financial_summary_month`, `financial_summary_year`, `collection_rate`, `retention_snapshot`, `group_utilisation`, and a `dashboard_report` bundle used by both the HTML page and the PDF export.
- New `/reports/` page: month/year controls, 4-tile financial snapshot (income / pending / expenses / net), collection-rate + retention cards, per-group utilisation table (`enrolled/max_students` + waiters), and a 12-row yearly table.
- New `GET /reports/download.pdf` renders the same data through the reportlab pipeline (`billing.services.pdf_service`) — reuses the shared header/footer/styles for a consistent look with receipts and tax certificates.
- Sidebar gains a "Informes" entry (`bar_chart` icon, admin-only). Non-admin whitelist extended with `reports_view` + `reports_pdf` for future teacher-facing rollout.
- 15 tests covering every service function, the endpoint happy paths, and the PDF byte signature.

</details>

<details id="v150">
<summary><strong>v1.5.0 — Expense Tracking</strong></summary>

Adds the second half of the finance loop — the app can now record every euro
that leaves the academy alongside every euro that comes in.

**Model**

- New `Expense` model with `description`, `category` (rent / salaries / supplies / utilities / marketing / software / insurance / taxes / other), `amount`, `expense_date`, and free-form `notes`.
- Optional recurring-template mode via `is_recurring=True` + `recurring_day` (1–28). Templates are never counted in monthly totals; instead a Beat job materialises a concrete `Expense` row (with a `generated_from` FK) on the first of every month, keeping historical reports honest and idempotent.

**Views + UI**

- New `/expenses/` page with month/year/category filters, an income vs expense summary (Ingresos / Gastos / Beneficio neto), a create form, and a compact table listing.
- Recurring templates surface in a dedicated section at the bottom of the page so admins can prune / edit them without hunting.
- Sidebar gains a "Gastos" entry with the `receipt_long` icon, visible to admins and non-admin teachers alike.
- Non-admin Teacher whitelist extended with `expenses_list`, `create_expense`, and `delete_expense`.

**Beat integration**

- New `billing.tasks.materialize_recurring_expenses_task` runs on day 1 at 06:30 Europe/Madrid — right after the payment-generation job so the month's ledger is complete before the admin opens the dashboard.

**Testing**

- 17 tests (`tests/unit/test_expenses.py` + `tests/integration/test_expense_views.py`) covering the model constraints, the `monthly_totals` service (empty, mixed, recurring-excluded), the `materialize_recurring` idempotency, and the full CRUD endpoint surface.

</details>

<details id="v140">
<summary><strong>v1.4.0 — Celery Beat Schedule</strong></summary>

**Beat schedule additions**

- `generate-monthly-payments`: runs `billing.tasks.generate_monthly_payments_task` on day 1 of every month at 06:00 Europe/Madrid. Wraps the existing `python manage.py generate_payments` command so Beat and the CLI share exactly one code path.
- `send-monthly-report`: runs `comms.tasks.send_monthly_report_task` on day 28 at 20:00 Europe/Madrid. Aggregates expected / collected / outstanding totals for the current month via a single `Payment.objects.aggregate` call and emails them to `SUPPORT_EMAIL` (skips gracefully when unset).

**Task discovery**

- New `billing/tasks.py` module (previously the app had no async tasks). Adds the module to Celery's autodiscover surface — no manual imports needed.
- Pre-existing birthday-emails and payment-reminders schedules remain unchanged.

**Testing**

- 5 unit tests for the new tasks (`tests/unit/test_beat_tasks.py`) covering the CLI-command wrap, the "no recipient" skip path, and custom-recipient forwarding.
- 8 Beat-schedule sanity tests (`tests/unit/test_celery_config.py`) that assert both new entries are present, run on the right day-of-month, target the correct queue, and are registered with the Celery app.

</details>

<details id="v130">
<summary><strong>v1.3.0 — PDF Invoice Generation</strong></summary>

**PDF service**

- New `billing/services/pdf_service.py` built on **reportlab** (pure-Python, no cairo/pango deps — deploys cleanly on Cloud Run and the testing VM without touching the base image).
- Three public functions: `generate_payment_receipt(payment)`, `generate_quarterly_summary(student, payments, quarter_label)`, and `generate_tax_certificate(parent, year)`. All three return raw PDF bytes so callers can attach to email, stream as an HTTP response, or upload to Cloud Storage without intermediate buffering.
- Shared `AcademyInfo` dataclass pulls business info from SiteConfiguration when populated; falls back to hard-coded defaults so a fresh install still produces a valid document.
- Consistent header (academy name + title + subtitle) and footer ("generated on…" + website) across all three document types, with the primary violet as the accent colour.

**Endpoints**

- New `GET /payments/<id>/receipt.pdf` streams a receipt directly (Content-Disposition: attachment; filename="recibo-<id>.pdf"). No JS wrapper — links can be embedded in any template.

**Backwards-compatible integration with comms**

- `comms.services.email_functions.generate_tax_certificate_pdf` now delegates to the new service. The old HTML+WeasyPrint block is retained as a defence-in-depth fallback (unreachable in practice because reportlab is now a hard dependency).

**Testing**

- 7 unit tests in `tests/unit/test_pdf_service.py` covering: PDF byte signature (`%PDF-…%%EOF`), missing payment date, missing parent, empty payment list for quarterly, zero-payment tax certificate, non-trivial size when payments exist.
- 2 integration tests in `tests/integration/test_receipt_view.py` for the `/payments/<id>/receipt.pdf` endpoint (200 + application/pdf, 404 on missing id).

</details>

<details id="v120">
<summary><strong>v1.2.0 — Google Sheets Integration</strong></summary>

**Service layer**

- New `core/services/google_sheets_service.py` with `GoogleSheetsService` (spreadsheet client + export methods) and `ExportResult` (never-raise result object). Two credential sources supported: inline JSON in `GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON` (recommended for Cloud Run + Secret Manager) or a JSON file path in `GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE`. Both are optional — `is_configured()` reports False when either the credential or `GOOGLE_SHEETS_SPREADSHEET_ID` is missing, and every entry point checks it before touching the network.
- Two export methods so far: `export_students()` writes the active-students snapshot (name, group, age, adult flag, GDPR, waiting-list flag, parents) and `export_payments(academic_year=None)` writes the payments table for the given year (defaults to current). Both overwrite their worksheet — the sheet is always an authoritative snapshot rather than an append-only log.

**Endpoints**

- New `POST /api/sheets/export/` with `?target=students|payments|both` (default `both`). Returns 200 on success, 400 for bad targets, 502 on partial export failures, and 503 when the integration is unconfigured so the frontend can surface a specific "not configured" message instead of a generic error.
- New management command `python manage.py export_to_sheets` with `--students / --payments / --academic-year / --students-sheet / --payments-sheet` flags. Runs headless from cron / Cloud Scheduler — no UI dependency.

**Settings + wiring**

- Three new optional settings (`GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON`, `GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE`, `GOOGLE_SHEETS_SPREADSHEET_ID`) — all read from env vars, all default to empty so the feature stays dormant until deliberately enabled.
- New `HistoryLog` action `sheets_exported` fires after every successful export.

**Testing**

- 14 unit tests in `tests/unit/test_google_sheets_service.py` covering configuration detection (inline / file / missing / malformed), the export-students and export-payments happy paths, and error-object propagation when the worksheet client raises.
- 6 integration tests in `tests/integration/test_sheets_views.py` covering method restriction, unconfigured-503, target validation, and the 502-on-partial-failure semantics.

</details>

<details id="v110">
<summary><strong>v1.1.0 — Waiting List & Group Capacity</strong></summary>

**Waiting list**

- New `Student.is_waiting` flag + `waiting_since` timestamp (auto-set on flip, cleared when unset). Waiting-list students still live in the same `students` table and keep their preferred `group` FK, but they don't count against the group's enrolled capacity and are excluded from `/students/`.
- New `/students/waiting/` page with per-student cards, FIFO ordering (`waiting_since` asc), a per-group filter, and a header capacity summary showing enrolled / max / available spots for every active group.
- Quick-assign action (`POST /students/<id>/assign/`) promotes a waiting student to enrolled in one click: flips `is_waiting=False`, creates a default full-time monthly `Enrollment`, and a pending enrollment-fee `Payment`. Refuses to run when the group is already at cap.
- Reverse action (`POST /students/<id>/wait/`) moves an enrolled student back onto the waiting list.
- Non-admin Teacher whitelist updated: `waiting_list`, `assign_from_waiting_list`, `add_to_waiting_list` are all reachable in view+edit modes for Teachers, matching the existing student-management authority level.

**Group capacity**

- New `Group.max_students` (PositiveIntegerField, default `0`) — a soft cap on enrolled students. `0` means "no cap" for backwards compatibility with every existing group.
- Group model gains `enrolled_count`, `waiting_count`, `available_spots`, `is_full` computed properties. `enrolled_count` excludes both inactive and waiting students, so `available_spots` reflects the number of real active seats free.
- `group_capacity_summary()` helper returns annotated capacity + waiter counts for the dashboard and the waiting-list page in a single query (uses conditional `Count` aggregates — no N+1).
- Group admin now shows `max_students` / `enrolled_count` / `available_spots` in the list view.

**Dashboard integration**

- New dashboard card highlighting `waiting_count` alongside a chip list of groups that have free spots *and* waiters (`has_room_for_waiters`), each linking through to the waiting-list page.
- Sidebar gains a "Lista de Espera" entry with a `hourglass_top` icon, visible to admins and non-admin Teachers alike.

**Notifications**

- Post-save signal on `Student` fires when `active` transitions `True → False`; if the group has waiters, a `HistoryLog` entry (`waiting_list_spot_open`) is written so the dashboard history dropdown surfaces the newly available spot.
- Three new `HistoryLog` action choices: `waiting_list_added`, `waiting_list_assigned`, `waiting_list_spot_open`.

**Testing**

- 14 unit tests in `tests/unit/test_waiting_list.py` covering the group capacity properties, `waiting_since` auto-set / clear, `group_capacity_summary`, and the pre-save `active` transition capture.
- 16 integration tests in `tests/integration/test_waiting_list_views.py` covering the list page, quick-assign happy path, cap enforcement, HTTP method restriction, waiting-list exclusion from the main students list, and the dashboard widget context.

</details>

<details id="v1013">
<summary><strong>v1.0.13 — Env-File Consolidation, Settings Simplification & Render Removal</strong></summary>

**Env files: 7 → 3, no overlays**

- Deleted stale env files: `.env`, `.env2`, `.env.old`, `.env.final`, `.env.testing_users`. The repo now ships exactly three self-contained env files — `.env.development`, `.env.testing`, `.env.production` — each one fully usable on its own.
- Workflow: rename the one you want active to `.env` before `docker compose up` (or `make up`). No more "which overlay won" detective work.
- `.env.production` is a template for **local prod-simulation only**. Real Cloud Run reads env from `--set-env-vars` + Secret Manager — never from a file.
- `make setup` now intelligently copies `.env.development → .env` if no `.env` exists yet.

**Settings.py simplified**

- Dropped 20 lines of conditional overlay-loading (`.env.development` / `.env.testing_users`). Now a single `load_dotenv(".env")` call.
- Removed the dead SQLite database fallback — PostgreSQL is the only supported backend.
- Removed the broken `urlparse` validation that was silently rejecting Cloud Run's socket-style `DATABASE_URL` (e.g. `postgres://user:pass@/db?host=/cloudsql/...`).
- Dropped all "Render, Heroku" comments and stale Spanish docstrings.
- Net change: ~50 lines shorter.

**entrypoint.sh rewritten**

- Removed the `IS_RENDER` boolean and every Render-themed log line. The new signal for "skip the postgres TCP wait" is `DATABASE_URL` presence (Cloud SQL via socket).
- Removed the `createsuperuser` block — admin access is delegated to Teachers with `ADMIN=True` via the `post_save` signal that mirrors `is_staff` + `is_superuser`.
- Always `exec "$@"` so the Dockerfile CMD (gunicorn) drives the server choice; the dev compose still overrides with `runserver`.
- ~120 lines shorter, single code path for all environments.

**docker-compose.testing.yml slimmed**

- Removed the redundant `env_file: .env.testing` override (compose reads `.env` now).
- Removed the duplicated `POSTGRES_DB/USER/PASSWORD` `environment:` blocks on both `db` and `web` — these come from `.env`.
- Removed the hardcoded password in the DB healthcheck — it now uses `${POSTGRES_PASSWORD}` from `.env`.
- ~30 lines shorter; only the two genuine differences from base remain (gunicorn command + isolated `testing_postgres_data` volume).

**gcp-cloudrun.yaml deleted**

- The alternative Cloud Run deployment manifest had placeholders and had drifted from `DEPLOYMENT.md`'s direct-`gcloud run deploy` workflow. Deleted to avoid a second source of truth.

**Documentation overhaul**

- Three `CLAUDE.md` gotchas rewritten (`load_dotenv` semantics, the new 3-file layout replacing the overlay system, teacher-seed contract).
- `README.md` updates: Quick Start uses the rename workflow, `.env template` is now a single superset block with per-section "applies to" notes (removed `DJANGO_SUPERUSER_*` and `ACADEMY_WHATSAPP` rows), Make Commands table fully synced with the actual Makefile (dropped fictitious `make test-sqlite/test-local/test-coverage/test-models/test-services/test-views/test-fast/test-k` targets, added the celery + cleanup blocks), file structure tree updated for the three-file env layout, dev auth description corrected, Configuration files table for QA, App Versioning section now correctly states "four places" (was "two").
- `DEPLOYMENT.md` testing-VM section no longer references the `.env.testing_users` overlay.
- `Makefile` versioning comment corrected to four places.
- `seed_teachers` warning message points to the new env file names.

**Testing VM live**

- Deployed to GCP Compute Engine `e2-micro` (us-east1-c, always-free tier) with a reserved static external IP `34.26.130.187`. Reachable at `http://34.26.130.187:8000/` over plain HTTP. Runs the full Docker Compose stack (db + redis + web + celery_worker + celery_beat) on top of a 2 GB swap file (the e2-micro only has 1 GB RAM).
- All three seeded teachers (Claudia, Silvia, John Doe) log in successfully; admin Teachers reach `/admin/` via their email + password.
- GCP billing budget alert set at €0.01 — fires on any non-free-tier spend.

</details>

<details id="v1012">
<summary><strong>v1.0.12 — Teacher Login, Password Reset & Non-Admin Whitelist</strong></summary>

**Authentication overhaul** (ships roadmap item v1.6)

- `core/views/auth.py`: login view now dispatches by `DJANGO_ENV`. **Development** still compares against `LOGIN_USERNAME`/`LOGIN_PASSWORD` and get-or-creates a matching Django superuser so `/admin/` keeps working. **Testing/production** authenticates against `auth.User` via `django.contrib.auth.authenticate` — Teachers log in with their email + hashed password.
- Google OAuth callback get-or-creates a Django superuser and links it to an existing Teacher by email so a single OAuth login grants both app and `/admin/` access through the same `ModelBackend`.
- `_finalize_session_login(...)` unifies session setup across env-var, Teacher, and OAuth paths — every successful login now goes through `django.contrib.auth.login` *and* the legacy `session["is_authenticated"]` flag.
- Logout calls `django.contrib.auth.logout(...)` and then flushes the session.

**Teacher ↔ auth.User link**

- `students/models.py`: new `Teacher.user` `OneToOneField(auth.User, null=True, on_delete=SET_NULL, related_name="teacher")`. Migration `students.0003_teacher_user` ships the field as nullable so existing rows survive.
- `Teacher.ensure_user(password=None)` — idempotent helper that get-or-creates the linked user, syncs name/email, mirrors `Teacher.admin` onto `is_staff` + `is_superuser`, and optionally sets a hashed password. Omitting the password leaves the user with `unusable_password` so they must use `/password-reset/`.
- `post_save` signal on Teacher mirrors `admin` / email / first_name / last_name onto the linked User on every save.

**Authorization (non-admin Teacher whitelist)**

- `core/middleware.py`: `SimpleAuthMiddleware` now does two layers. Layer 1 (authentication) is unchanged; layer 2 (authorization) restricts non-admin Teachers to the `NON_ADMIN_ALLOWED_URL_NAMES` whitelist — admin-only routes redirect to the dashboard with a flash message, or return `{"success": False, "error": ...}` JSON 403 on `/api/*`.
- Public prefixes list now includes `/password-reset/` so locked-out teachers can still reach the reset flow.
- `core/context_processors.py`: exposes `is_admin_user` / `is_non_admin_teacher` flags so templates can hide admin-only UI (`base.html` swaps Payments/Apps/Database for Fun Friday in the sidebar; `management.html` becomes read-only).

**Password reset flow**

- New `core/views/password_reset.py`: branded subclasses of Django's built-in `PasswordResetView` / `Done` / `Confirm` / `Complete` plus a `build_reset_link(request, user)` helper.
- New URL patterns: `/password-reset/`, `/password-reset/sent/`, `/password-reset/confirm/<uidb64>/<token>/`, `/password-reset/complete/`.
- New branded templates under `project/templates/registration/` (`reset_base.html`, `password_reset_form.html`, `password_reset_done.html`, `password_reset_confirm.html`, `password_reset_complete.html`, plus `password_reset_email.txt` / `password_reset_subject.txt`) and a new HTML email template at `core/templates/emails/password_reset.html`.
- Login page renders "¿Has olvidado tu contraseña?" link only when `password_reset_available` is true (i.e. non-dev environments).

**Teacher seeding**

- New `core/management/commands/seed_teachers.py`: idempotent Teacher + linked-User creation from `TEACHER_SEED_<N>_*` env vars (numbered from 1, iteration stops at the first missing `FIRST_NAME`). Re-running updates name/phone/admin but never overwrites a password an admin later changed.
- `entrypoint.sh` invokes `python project/manage.py seed_teachers` on container start when `DJANGO_ENV` is `testing` or `production`. No-op in development.

**Settings**

- `project/project/settings.py`: after the base `.env` load, conditionally loads `.env.development` (`DJANGO_ENV=development`) or `.env.testing_users` (`DJANGO_ENV=testing`) as an overlay with `override=True`. Docker-injected process env vars still win over both. Both filenames are gitignored via `.env*`.
- Added explicit `LOGIN_URL`, `LOGIN_REDIRECT_URL`, `LOGOUT_REDIRECT_URL` so Django's auth helpers (and the password-reset `success_url` chain) resolve consistently.

**Tests (+49, suite at 623)**

- New `tests/integration/test_password_reset.py` (10): full reset round-trip including email rendering and the public-URL middleware exemption.
- New `tests/integration/test_teacher_auth_flow.py` (21): dev vs non-dev login dispatcher, OAuth user creation/linking, non-admin Teacher whitelist enforcement, dashboard role gating.
- New `tests/unit/test_seed_teachers_command.py` (8): creation, idempotent update, password persistence rule, gap-stop iteration.
- New `tests/unit/test_teacher_user_sync.py` (10): `Teacher.ensure_user()` paths and the `post_save` mirror signal.

</details>

<details id="v1011">
<summary><strong>v1.0.11 — Testing Environment Fixes, CI Hardening & Static File Cleanup</strong></summary>

**Testing environment**

- `docker-compose.testing.yml`: added explicit `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` overrides to both `db` and `web` services — the base `docker-compose.yml` uses `.env` credentials while the overlay uses `.env.testing` credentials; without these overrides the `db` container initialised with dev credentials while the `web` container tried to connect with testing credentials
- `settings.py`: `load_dotenv(override=True)` → `override=False` — Docker `environment:` values now take precedence over the volume-mounted `.env` file; `override=True` was silently overwriting credentials injected by the compose overlay
- `core/context_processors.py`: added `hasattr(request, "session")` guard before `request.session.get("username")` — prevents `AttributeError` 500 errors in admin views and error-handler requests that bypass `SessionMiddleware`

**Static files**

- `STATICFILES_DIRS = [BASE_DIR / "static"]` removed from `settings.py`; all static assets now live under `project/core/static/` (served via `APP_DIRS=True`) — no separate `STATICFILES_DIRS` needed
- Moved to `project/core/static/`: `css/admin_custom.css`, `css/email.css`, `images/logo_white_bg.png`
- Deleted legacy `project/static/` assets: `apple-touch-icon.png`, `favicon-32x32.png`, `favicon.ico`, `images/logo.png`

**CI/CD — new jobs and workflows**

- `ci.yml` lint job: added `pip-audit` CVE scan and Hadolint Dockerfile lint
- New CI job — **Docker build**: validates `Dockerfile` builds cleanly on every push/PR (with GHA cache)
- New CI job — **Trivy filesystem scan**: scans Python deps + filesystem for HIGH/CRITICAL CVEs; uploads SARIF to GitHub Security tab
- New CI job — **Docker publish**: on push to `main`/`testing`, builds and pushes image to GHCR (`ghcr.io/starseeker-code-public/five-a-day:<branch>` + `sha-<sha>`), then runs Trivy image scan
- `codecov-action` upgraded v4 → v5
- New `dependabot-auto-merge.yml`: automatically merges Dependabot minor/patch PRs once CI passes
- New `dependency-review.yml`: blocks PRs that introduce a HIGH/CRITICAL CVE dependency
- New `scorecard.yml`: OSSF Scorecard supply-chain security grading (weekly + on push to `main`); results published to GitHub Security tab

**Admin**

- `#nav-sidebar` right padding set to `1rem` in `admin_custom.css`

</details>

<details id="v1010">
<summary><strong>v1.0.10 — Branded Admin Theme, White-Bg Favicon & Social Meta</strong></summary>

**Social sharing & branding**

- Logo changed to `logo_white_bg.png` across README, `base.html` favicon, apple-touch-icon, Open Graph, and Twitter Card — white background improves rendering in light-themed link preview cards
- Favicon regenerated from `logo_white_bg.png`: multi-size ICO (16/32/48/64px), `favicon-32x32.png`, and `apple-touch-icon.png` (180×180) — dropped in both `project/static/` and `project/core/static/`
- `og:image:secure_url` added alongside `og:image` for Facebook's HTTPS-explicit crawler
- `twitter:image:alt` added to Twitter Card block
- Schema.org JSON-LD block added to `base.html` (`@type: WebApplication`, provider as `EducationalOrganization`) — covers Google Search previews, Gmail, and Google Chat link unfurling

**Django admin — Five a Day theme**

- `project/templates/admin/` created; `TEMPLATES.DIRS` now points to `project/templates/` so project-level overrides take priority over Django's built-ins
- `admin/base_site.html` — violet gradient header (`#4c1d95` → `#7c3aed`) with `logo_white_bg.png`, loads `admin_custom.css` on every admin page
- `admin/login.html` — card-style login page: logo, "Gestión Académica · Albacete" subtitle, Spanish field labels (Usuario / Contraseña / Entrar)
- `admin/index.html` — welcome banner with logo + Spanish action labels (Añadir / Editar / Ver / Acciones recientes)
- `project/static/css/admin_custom.css` — full CSS-variable override of Django admin (violet/purple palette, Trebuchet MS font, styled login card, fieldset headers, welcome banner)
- `core/admin.py` — `site_title` → "Five a Day · Admin", `index_title` → "Panel de administración"

**Dependencies (Dependabot)**

- `gunicorn` upgraded 22.0.0 → 23.0.0 (constraint widened `<23` → `<24`)
- `pandas` upgraded 2.3.3 → 3.0.2 (constraint widened `<3` → `<4`)

</details>

<details id="v109">
<summary><strong>v1.0.9 — Test Suite Restructure, 96% Coverage & CI Coverage Gates</strong></summary>

**Testing**

- Reorganised flat `project/tests/` into two subdirectories: `unit/` (direct calls, no HTTP stack) and `integration/` (Django test client through middleware)
- Expanded test suite from ~280 tests to **574 tests** across 30 files — coverage raised from ~70% to **96%**
- New unit test files: `test_tasks.py`, `test_student_view_internals.py`, `test_decorators.py`, `test_error_handlers.py`, `test_qa_error_middleware.py`, `test_payment_helpers.py`, `test_testing_tools_helpers.py`, expanded `test_email_functions.py`, `test_email_service.py`, `test_context_processors.py`, `test_models.py`, `test_services.py`
- New integration test files: `test_app_form_views.py`, `test_payment_views.py`, `test_student_views.py`, `test_management_views.py`, `test_testing_tools.py`, `test_auth_oauth.py`, `test_dashboard_views.py`, `test_parent_views.py`, `test_schedule_views.py`, `test_fun_friday_attendance_views.py`, `test_todo_views.py`, `test_support_views.py`

**CI/CD — Coverage enforcement**

- CI coverage step: hard floor ≥ 75% (fails CI), warning annotation < 90% (CI still passes)
- Pre-commit hook: `pytest-coverage` hook blocks commits when coverage drops below 75%
- `make test-cov-gate` target added for pre-commit integration
- Coverage threshold `fail_under = 75` set in `pyproject.toml`

**Dashboard**

- Quote-of-the-day rewritten: in-memory batch cache fetches up to 50 quotes from zenquotes.io per API call; each page load pops one; cookie stores the last served quote as ASCII fallback
- Thread-safety comment added to `_quotes` module-level list

**Developer tooling**

- `/pc-run` Claude skill: runs pre-commit in a loop, fixes failures, then asks `y` / `X.Y.Z` / `n` for version bumping
- `update-readme` skill: added Coverage Report subsection generation (§ k.1)
- Makefile: all em dashes and right-arrow Unicode replaced with ASCII equivalents
- `comms/tasks.py`: `raise Exception(...)` tightened to `raise RuntimeError(...)` across all four failure paths
- Mid-file imports with `# noqa: E402` removed from all test files — moved to file-top imports

</details>

<details id="v108">
<summary><strong>v1.0.8 — Lean README, docs/ Purge & Test-Suite Hygiene</strong></summary>

**README / docs**

- Removed the "Key objectives" bullet list and the "Live status for each environment…" subtitle — the header + intro sentence + Project Status table already communicate that
- Shortened the project intro line
- Recent Versions table rewritten to ≤10-word headline phrases; the dense per-version writeups now live only in the Version History `<details>` blocks below (where you're reading this)
- README header image sourced from `project/static/images/logo.png` now that the old `docs/resources/logo.png` is gone

**`docs/` asset cleanup**

- Deleted every tracked binary under `docs/` — UI screenshots, Gantt PNG/SVGs, legacy logos. `docs/` is already in `.gitignore`, so nothing gets re-tracked
- No remaining references to the deleted paths; header image is the only asset the README needed from there

**Documentation convention**

- `update-readme` skill (Step 3.1.c) and the README-maintenance checklist in `CLAUDE.md` now mandate **extremely brief** Recent Versions rows (≤10 words, headline only). Long-form content belongs in the Version History block. Future runs of the skill will enforce this.

**Test-suite hygiene**

- Removed `test_google_oauth_prefix_public` — the CI was failing because `core/views/auth.py::google_oauth_redirect` gracefully returns `redirect("login")` when `GOOGLE_CLIENT_ID` is unset (CI's state), which the test's assertion couldn't distinguish from a middleware-level block. The remaining `TestPublicPaths` cases (static, health, login) still cover the middleware's exemption logic.

**Developer tooling**

- `make pc-run` log line for the auto-staged `uv.lock` trimmed from `"Staged updated uv.lock — next git commit will not be blocked by it"` to `"Staged updated uv.lock"` — the explanatory tail was redundant in practice

</details>

<details id="v107">
<summary><strong>v1.0.7 — Favicon, Social Metadata & CI Test Fixes</strong></summary>

**Social sharing & branding**

- Multi-resolution `favicon.ico` (16/32/48/64/128/256) generated from `project/static/images/logo.png` — dropped in both `project/static/` and `project/core/static/` so both STATICFILES_DIRS paths serve it
- `base.html` now includes full social-sharing metadata: `<meta name="description">`, `<meta name="author">`, `<meta name="theme-color" content="#6d28d9">` (matches the violet palette), `apple-touch-icon`, full Open Graph set (`og:type`, `og:site_name`, `og:title`, `og:description`, `og:image`, `og:image:alt`, `og:url`, `og:locale`), and Twitter Card summary tags
- Every content field is wrapped in an overridable Django block (`meta_description`, `og_title`, `og_description`, `og_image`, `twitter_title`, `twitter_description`, `twitter_image`) so per-page templates can tailor link previews without touching `base.html`

**Test-suite fixes**

- `settings_test.py` now explicitly sets `SECURE_SSL_REDIRECT = False`, `SECURE_HSTS_SECONDS = 0`, `SESSION_COOKIE_SECURE = False`, `CSRF_COOKIE_SECURE = False` — the CI environment runs with `DJANGO_DEBUG=False`, which activated the production SSL redirect and turned every test request into a 301 to `https://testserver/...`. The test settings are now self-contained and correct regardless of `DJANGO_DEBUG`.
- `pytest.ini` adds `filterwarnings = ignore:No directory at:UserWarning` to silence the 142 WhiteNoise warnings that were emitted once per test request (the `staticfiles/` directory only exists after `collectstatic`, which isn't run before tests)

**Dashboard reliability**

- Zenquotes fetch in `core/views/dashboard.py` now targets `https://zenquotes.io/api/quotes` (no trailing slash — the old URL was getting 301-redirected) with `follow_redirects=True` as a guard against future URL changes
- Silent `except Exception: pass` replaced with proper `logger.warning(...)` calls — failures are still non-fatal but now visible in logs

**CI tooling**

- `mypy` job in `ci.yml` now sets `DJANGO_SETTINGS_MODULE=project.settings`, `DJANGO_DEBUG=True`, a dummy `DJANGO_SECRET_KEY`, and `PYTHONPATH=project` — `django-stubs` imports `settings.py` at load time, which previously raised the production secret-key guard
- `make version x.y.z` now also updates the README version badge via `sed`, regenerates `uv.lock` via `uv lock --quiet`, and prints a reminder to run the `update-readme` skill afterwards; running `make version` with no arg now shows both `pyproject.toml` and the README badge side-by-side and warns if they've drifted
- `make pc-run`'s auto patch-bump now also rewrites the README badge and regenerates `uv.lock` — the existing `git add uv.lock` tail stages the refreshed lockfile automatically

</details>

<details id="v106">
<summary><strong>v1.0.6 — Documentation Skill & Doc Overhaul</strong></summary>

**Documentation agent**

- New `update-readme` Claude skill at `.claude/skills/update-readme/SKILL.md` — routes staged files to the right docs (main README, CLAUDE.md, DEPLOYMENT.md, docs/, per-app READMEs), applies per-file checklists, and sweeps for stale references across the full documentation tree

**README overhaul**

- `readme.md` → `README.md` rename (case-sensitive file systems matter on GCP)
- Major reorganization of sections; expanded Environment Variables Reference; tightened Recent Versions table to 3 rows; populated Developer Tooling and Make Commands tables
- `.env template` is now the single authoritative source for local env-var structure, lives inline in the README as a fenced `bash` block

**Secrets hygiene**

- Removed `.env.testing.example` (its content now lives only inline in the README `.env template` block)
- `.gitignore` tightened: `.env*` matches everything, no `!.env.example` exception, no `.env*.example` carve-outs

**CI workflow refinements**

- `auto-merge.yml` — improved commit detection and PR creation for the `development` → `testing` → `main` cascade
- `notify-production.yml` — richer production deployment notification email with commit info and next-step `gcloud` commands

**Per-app docs**

- `project/core/README.md` and `project/comms/README.md` touched up to match post-refactor structure

</details>

<details id="v105">
<summary><strong>v1.0.5 — CI/CD Pipeline & Public Repo Hardening</strong></summary>

**GitHub Actions CI/CD** (new — see [docs/GITHUB.md](docs/GITHUB.md))

- `ci.yml` — three parallel jobs on every push/PR: Ruff + Bandit lint, mypy type check, pytest against a PostgreSQL 16 service container with coverage uploaded to Codecov
- `auto-merge.yml` — hourly cron that merges `development` → `testing` after 24 h of inactivity and CI passing, then auto-creates a PR `testing` → `main`
- `codeql.yml` — weekly Python security analysis (OWASP Top 10, Django-specific queries)
- `notify-production.yml` — emails `hellofiveaday@gmail.com` on every push to `main` with commit info and `gcloud` deploy instructions
- Owner email notifications when `development` → `testing` merge lands and a PR is opened to `main`
- `dependabot.yml` — grouped weekly Python and GitHub Actions updates targeting `development`
- `CODEOWNERS` — auto-request reviews from both owner accounts

**Public-repo hardening**

- Branch protection rules documented for `main` (14 protections) and `testing` (minimal)
- Secret scanning + push protection + CodeQL enabled (all free for public repos)
- Fork PR workflow restriction, read-only default workflow permissions, block-approvals-from-Actions
- `SECURITY.md` + `CODEOWNERS` + `LICENSE` required-file checklist in [docs/GITHUB.md](docs/GITHUB.md)

**Developer tooling**

- `make pc-run` auto-stages regenerated `uv.lock` as the final step — next `git commit` is no longer blocked by the lock file

</details>

<details id="v104">
<summary><strong>v1.0.4 — GCP Migration Plan, Quote Generator, Celery</strong></summary>

**GCP migration plan** (new — see [DEPLOYMENT.md](DEPLOYMENT.md))

- Full Cloud Run + Cloud SQL architecture documented
- Three environments: local Docker (dev), Compute Engine e2-micro free tier (testing), Cloud Run + Cloud SQL (production)
- Cost estimate: ~$15-27/month for production, $0/month for testing
- Celery replacement strategy using Cloud Scheduler + Cloud Run Jobs
- Cleaned legacy Render config — `render.yaml` removed; commented nginx and pgAdmin services removed from `docker-compose.yml`

**Dashboard enhancement**

- Inspirational quote generator on `/home` — fetches two daily quotes from `zenquotes.io`, stores them in a 48 h cookie, rotates daily (day 0 shows quote 1, day 1+ shows quote 2), graceful fallback to the default Spanish subtitle on API failure

**Developer tooling**

- `make version x.y.z` — positional argument (replaces `V=x.y.z`) with confirmation guard before writing
- `make pc-run` — renamed from `pre-commit-run`; after a clean pass, prompts to auto-increment the patch version in `pyproject.toml` and `project/settings.py`

**Celery**

- Celery worker and beat containers added to `docker-compose.yml` with correct permissions and health checks
- Several payment and enrollment issues fixed

</details>

<details id="v103">
<summary><strong>v1.0.3 — Test Coverage Expansion (70%)</strong></summary>

**Testing**

- 40+ new tests added across 13 new test files — overall suite around 280+ tests
- Coverage raised to **70%** across `core`, `students`, `billing`, `comms`
- New test files: `test_auth_views.py`, `test_app_form_views.py`, `test_constants.py`, `test_create_payment_views.py`, `test_exports.py`, `test_forms.py`, `test_parent_views.py`, `test_payment_views.py`, `test_schedule_views.py`, `test_student_forms.py`, `test_student_views.py`, `test_transactions.py`
- Additional parametrized test cases for email-form views and error pages

**Coverage tooling**

- Coverage badge pulled dynamically from Codecov (CI workflow uploads `coverage.xml` on every run)
- `make coverage-badge` retained for offline SVG generation

</details>

<details id="v102">
<summary><strong>v1.0.2 — UV Migration & Developer Tooling</strong></summary>

**Dependency management**

- Replaced Poetry with UV (see [docs/UV.md](docs/UV.md))
- `uv.lock` replaces `poetry.lock`
- All Make commands updated to use `uv run`

**Developer tooling**

- **Ruff** — unified lint + format (replaces flake8, isort, black)
- **mypy** with `django-stubs` — static type checking
- **bandit** — Python security linter
- **pip-audit** — dependency CVE scanning
- **pytest-xdist** — parallel test execution (`-n auto`)
- **pytest-randomly** — randomized test order with reproducible seeds
- **pytest-cov** — coverage reports (HTML + XML + terminal)
- **pre-commit** hooks — Ruff, mypy, bandit on every commit

All tools configured in `pyproject.toml` — single source of truth.

</details>

<details id="v101t">
<summary><strong>v1.0.1t — QA Testing Environment</strong></summary>

**Testing infrastructure**
- QA Docker Compose overlay (`docker-compose.testing.yml`) — Gunicorn, `DEBUG=False`, separate DB volume
- `.env.testing` with dedicated credentials and `DJANGO_ENV=testing`
- Database seeding command (`seed_testdata`) — 15+ students, parents, enrollments, payments
- HTTPS documentation (`HTTPS.md`) — local Docker (Nginx + self-signed cert) and GCP Cloud Run

**Testing dashboard (`/testing/`)**
- Project info card — version, environment, last commit (branch, hash, author, date)
- Error reporting toggle — sends unhandled exceptions to SUPPORT_EMAIL with full traceback
- Database seeding UI — seed or wipe-and-reseed via AJAX
- Backlog — create tasks with priority, each emailed to support automatically

**Access control**
- `qa_access_required` decorator in `core/decorators.py`
- Gated by `DJANGO_ENV=testing` + `DEBUG=False` + the request is made by a logged-in Teacher (admin or not)
- Returns 404 (not 403) for unauthorized users — page appears not to exist
- Sidebar icon hidden for non-Teacher sessions via context processor

**Bug fixes**
- Added `STATICFILES_DIRS` for `project/static/` — email CSS was missing from collectstatic manifest
- Added `SECURE_PROXY_SSL_HEADER` for HTTPS behind reverse proxies
- `QAErrorEmailMiddleware` for automated error reporting to support email

</details>

<details id="v100">
<summary><strong>v1.0.0 — Architecture Refactor & Test Suite</strong></summary>

**Architecture**
- Split monolithic `core` app into 4 apps: `students`, `billing`, `comms`, `core`
- Created service layer: EnrollmentService, PaymentService, PricingService
- Split 3,648-line views.py into 12 focused modules
- Fixed module-level querysets, wildcard imports, dual pricing source of truth

**Frontend**
- Replaced 1,178-line pre-compiled Tailwind with CDN + custom violet palette config
- Extracted ~1,400 lines of inline JS into 13 static modules
- Removed `#webcrumbs` CSS scoping wrapper
- base.html: 610 lines reduced to 305 lines

**Testing**
- 132 pytest tests: 41 model, 26 service, 65 view tests
- Tests run against PostgreSQL (same as production)
- Found and fixed Payment `active` field bug

**Templates**
- Renamed all Spanish-named email templates to English (e.g., `matricula_niño.html` -> `enrollment_child.html`)

**Documentation**
- Comprehensive README with all sections
- Per-app README.md files (core, students, billing, comms)
- CLAUDE.md for AI-assisted development
- DEPLOYMENT.md for Google Cloud Platform

</details>

<details id="v0302">
<summary><strong>v0.30.2 — Docker & History System</strong></summary>

- Docker Compose with PostgreSQL 16 + Django
- Makefile with 40+ commands for development workflow
- HistoryLog system for tracking user actions (capped at 1,000 entries)
- GDPR tracking for adult students
- Improved entrypoint script for Docker

</details>

<details id="v0290">
<summary><strong>v0.29.0 — Enrollment & Email System</strong></summary>

- Enrollment system with 3 plans (monthly full/part-time, quarterly)
- Discount engine: language cheque, sibling, quarterly, June end-of-year
- Adult student support with separate pricing
- 12 email templates with preview and test-send
- Fun Friday attendance tracking
- Support ticket system

</details>

### Roadmap

<details id="roadmap">
<summary><strong>Click to expand roadmap (all shipped)</strong></summary>

All v1.1 – v1.14 milestones are shipped, and production went live on Cloud Run
in v1.14.7. Post-v1.12 evolution happens in directly-scoped work rather than the
numbered roadmap; add new items here when they're planned and dated.

Two pieces of work are tracked but deliberately deferred (see [CLAUDE.md](CLAUDE.md)):

- **`comms` reaches up into `core` twice** — `comms/tasks.py` lazily imports `core.schedule_utils` and `core.models.FunFridayScheduledSend`, which reverses the documented dependency flow. Both imports are function-body, so there is no cycle today; the cost is coupling.
- **Django 6.1 email deprecations** — the whole `EMAIL_*` settings family is superseded by `MAILERS`, and `EmailMessage.send(fail_silently=...)` is deprecated (56 `RemovedInDjango70Warning`s in the suite). Nothing breaks before Django 7.0, but this must land before the `<7` bound is lifted.

</details>

---

## Tech Stack

### Backend

| Technology | Version | Purpose |
|-----------|---------|---------|
| [![Python](https://img.shields.io/badge/Python-3.12+-3776ab?style=flat-square&logo=python&logoColor=white)](https://python.org) | 3.12+ | Runtime |
| [![Django](https://img.shields.io/badge/Django-6.1-092e20?style=flat-square&logo=django&logoColor=white)](https://djangoproject.com) | 6.1 | Web framework — pinned `>=6.0.8,<7` so a major can't land unreviewed |
| [![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?style=flat-square&logo=postgresql&logoColor=white)](https://postgresql.org) | 16 (Alpine) | Database (production, development, and testing) |
| Celery | 5.6.3 | Async task queue (eager mode without Redis, full async with Redis) |
| Celery Beat | (bundled with Celery) | Scheduled task execution — 8 periodic tasks (birthday emails, payment generation, recurring expenses, monthly report, Fun Friday drain, backlog cleanup). Production has no Beat process: Cloud Scheduler runs the equivalent management commands as Cloud Run Jobs |
| Redis | 7 (Alpine) | Message broker + result backend for Celery, and the Django cache backend (rate limiter) |
| Gunicorn | 23.0.0 | Production WSGI server |
| WhiteNoise | 6.12.0 | Static file serving in production (hashed + compressed manifest storage) |

### Frontend

| Technology | Purpose |
|-----------|---------|
| [Tailwind CSS](https://tailwindcss.com/) (CDN) | Utility-first CSS with custom violet primary palette |
| [Google Fonts](https://fonts.google.com/) | Material Symbols Outlined (icons), Montserrat Alternates (login), Parisienne (login accent) |
| Vanilla JavaScript | 16 static modules — zero build tools, no framework |

### Infrastructure & Deployment

| Technology | Purpose |
|-----------|---------|
| Docker | Multi-stage build, non-root `django` user |
| Docker Compose | Service orchestration (PostgreSQL + Redis + Django + Celery worker + Beat) |
| Google Cloud Platform | Production: Cloud Run + Cloud SQL (`europe-southwest1`) + Cloud Scheduler + Secret Manager. Testing: Compute Engine `e2-micro` (always-free tier) |
| Gmail SMTP | Email sending (app password authentication) |
| Google OAuth 2.0 | Optional admin authentication |
| Make | 48 development commands (`make help`) |

### Python Dependencies

| Package | Purpose |
|---------|---------|
| `django-cors-headers` | CORS handling for future API consumers |
| `django-filter` | Query filtering utilities |
| `django-extensions` | Development utilities (shell_plus, graph_models) |
| `django-gsheets` + `gspread` | Google Sheets integration (v1.2) |
| `django-redis` | Redis cache backend (v1.4) |
| `django-storages` | Cloud storage backends (future) |
| `pandas` | Data processing for exports |
| `openpyxl` | Excel file generation (.xlsx) |
| `httpx` | HTTP client for external API calls |
| `psycopg2-binary` | PostgreSQL database adapter |
| `dj-database-url` | Database URL parsing for cloud deployments |
| `python-dotenv` | Environment variable loading from .env |
| `markdown` | Markdown rendering |
| `pytest` + `pytest-django` | Testing framework |
| `pytest-xdist` | Parallel test execution (`-n auto`) |
| `pytest-randomly` | Randomized test ordering (catches order-dependent bugs) |
| `pytest-cov` + `coverage-badge` | Coverage reporting + SVG badge generation |
| `reportlab` | PDF generation — receipts, quarterly summaries, tax certificates (v1.3) |
| `pyotp` + `qrcode` | TOTP two-factor authentication + enrolment QR codes (v1.13) |
| `djangorestframework` | Serializer/API primitives |
| `django-environ` | Typed environment parsing |
| `django-debug-toolbar` | SQL/query inspection in development |
| `gunicorn` | Production WSGI server (`--chdir project`, see the Dockerfile `CMD`) |
| `whitenoise` | Hashed + compressed static serving |
| `cryptography` / `sqlparse` / `idna` / `urllib3` / `pip` | Transitive dependencies pinned to patched ranges to clear `pip-audit` CVEs — see the comments in `pyproject.toml` |

### Developer Tooling

| Tool | Purpose |
|------|---------|
| [UV](https://docs.astral.sh/uv/) | Dependency management (replaces Poetry). PEP 621, `uv.lock`. See `docs/UV.md` |
| [Ruff](https://docs.astral.sh/ruff/) | Linting + formatting (replaces flake8, black, isort). Config in `pyproject.toml` |
| [mypy](https://mypy-lang.org/) + `django-stubs` | Static type checking with Django ORM support |
| [bandit](https://bandit.readthedocs.io/) | Security linter (hardcoded secrets, SQL injection, etc.) |
| [pip-audit](https://github.com/pypa/pip-audit) | Dependency vulnerability scanning against PyPI CVE database |
| [pre-commit](https://pre-commit.com/) | Git hooks: ruff, ruff-format, mypy, bandit |

---

## Database Schema

### ER Diagram

```mermaid
erDiagram
    Teacher {
        int id PK
        string first_name
        string last_name
        string email UK
        string phone
        bool active
        bool admin
        int user_id FK
        string two_factor_secret
        bool two_factor_enabled
        json two_factor_backup_codes
    }

    Group {
        int id PK
        string group_name UK
        string color
        int teacher_id FK
        int max_students
        bool active
    }

    Parent {
        int id PK
        string first_name
        string last_name
        string dni UK
        string phone
        string email
        string iban
        bool sms_opt_in
    }

    Student {
        int id PK
        string first_name
        string last_name
        date birth_date
        string gender
        bool is_adult
        string email
        string phone
        string school
        text allergies
        bool gdpr_signed
        int group_id FK
        bool active
        bool is_waiting
        datetime waiting_since
        date withdrawal_date
        text withdrawal_reason
    }

    StudentParent {
        int id PK
        int student_id FK
        int parent_id FK
    }

    SiteConfiguration {
        int id PK
        decimal children_enrollment_fee
        decimal adult_enrollment_fee
        decimal full_time_monthly_fee
        decimal part_time_monthly_fee
        decimal adult_group_monthly_fee
        decimal language_cheque_discount
        decimal quarterly_enrollment_discount
        decimal old_student_discount
        decimal returning_student_enrollment_discount
        decimal sibling_discount
        decimal june_discount
        decimal full_year_bonus
        decimal half_month_discount
        decimal one_week_discount
        decimal three_week_discount
    }

    EnrollmentType {
        int id PK
        string name UK
        string display_name
        decimal base_amount_full_time
        decimal base_amount_part_time
        bool active
    }

    Enrollment {
        int id PK
        int student_id FK
        int enrollment_type_id FK
        date enrollment_period_start
        date enrollment_period_end
        string academic_year
        string schedule_type
        string payment_modality
        bool has_language_cheque
        bool is_sibling_discount
        decimal enrollment_amount
        decimal discount_percentage
        decimal final_amount
        string status
        date enrollment_date
    }

    Payment {
        int id PK
        int student_id FK
        int parent_id FK
        int enrollment_id FK
        string payment_type
        string payment_method
        decimal amount
        string payment_status
        date due_date
        date payment_date
        string concept
        string reference_number
        string stripe_session_id
        string stripe_payment_intent
    }

    TodoItem {
        int id PK
        string text
        date due_date
    }

    HistoryLog {
        int id PK
        string action
        string message
        string icon
        datetime created_at
    }

    ScheduleSlot {
        int id PK
        int row
        int day
        int col
        int group_id FK
    }

    FunFridayAttendance {
        int id PK
        int student_id FK
        date date
    }

    FunFridayScheduledSend {
        int id PK
        json recipients
        text activity_description
        datetime scheduled_for
        datetime sent_at
    }

    Expense {
        int id PK
        string description
        string category
        decimal amount
        date expense_date
        bool is_recurring
        string recurring_frequency
        int recurring_day
        int recurring_month
        string recurring_weekdays
        int generated_from_id FK
    }

    ParentSessionToken {
        int id PK
        int parent_id FK
        string token
        datetime expires_at
        datetime used_at
    }

    AuditLog {
        int id PK
        int actor_id FK
        string actor_label
        string action
        string model
        string object_id
        string object_label
        json changes
        datetime created_at
    }

    BacklogTask {
        int id PK
        string title
        text description
        string priority
        string status
        string created_by
        datetime updated_at
    }

    QAConfiguration {
        int id PK
        bool error_email_enabled
    }

    Teacher ||--o{ Group : "teaches"
    Group ||--o{ Student : "contains"
    Student }o--o{ Parent : "has parents"
    Student ||--o{ StudentParent : ""
    Parent ||--o{ StudentParent : ""
    Student ||--o{ Enrollment : "enrolls in"
    EnrollmentType ||--o{ Enrollment : "type of"
    Student ||--o{ Payment : "pays"
    Parent ||--o{ Payment : "responsible for"
    Enrollment ||--o{ Payment : "covers"
    Group ||--o{ ScheduleSlot : "assigned to"
    Student ||--o{ FunFridayAttendance : "attends"
    Parent ||--o{ ParentSessionToken : "portal magic link"
    Expense ||--o{ Expense : "generated from recurring"
```

**Not shown:** `Teacher.user_id` points at Django's built-in `auth.User`, and
`AuditLog.actor_id` at the same table — both are outside this diagram.
`BacklogTask` and `QAConfiguration` are QA-only and stand alone with no foreign keys.

### Key Constraints

| Constraint | Model | Rule |
|-----------|-------|------|
| Singleton | SiteConfiguration | Always pk=1, cannot be deleted |
| Unique active | Enrollment | Only one active enrollment per student |
| Unique pair | StudentParent | (student, parent) |
| Unique pair | FunFridayAttendance | (student, date) |
| Unique triple | ScheduleSlot | (row, day, col) |
| Unique | Teacher.email, Group.group_name, Parent.dni, EnrollmentType.name | |

---

## Development & Docker

### Quick Start

```bash
# Clone the repository
git clone https://github.com/starseeker-code-public/five-a-day.git
cd five-a-day

# Create the three env files from the template below — all three are
# gitignored, so they don't exist after clone. You can edit them in place
# and pick which one is active by renaming it to `.env`:
#   .env.development   for local Docker dev
#   .env.testing       for the QA stack (testing VM or local prod-simulation)
#   .env.production    template for Cloud Run (real prod reads env from
#                      Secret Manager + --set-env-vars, not this file)

# Activate development locally:
mv .env.development .env
make up                # Start PostgreSQL + Redis + Django + Celery → http://localhost:8000
```

**Local development (no Docker):**

```bash
uv sync                # Install dependencies
cd project
python manage.py migrate
python manage.py runserver
```

> **Important** — only ONE file named `.env` is read by `settings.py`. The `.env.*` files in the repo are alternative environments; you switch by renaming. Before starting, the active `.env` must set at minimum:
>
> - `DJANGO_ENV` — `development` / `testing` / `production`
> - `DJANGO_DEBUG` — `True` in development, `False` everywhere else
> - `POSTGRES_PASSWORD` — required for database connection
> - `DJANGO_SECRET_KEY` — generate with `python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'`

### .env template

All `.env*` files are gitignored. The repo ships three of them (`.env.development`, `.env.testing`, `.env.production`) — each is self-contained, and the one you want active is renamed to `.env` before bringing the stack up.

The template below is the **superset** of all keys. Not every key applies to every environment — the comments call out which environment each block is for. Use it as a reference to author your three env files.

```bash
# ============================================================================
# DJANGO  (all environments)
# ============================================================================
DJANGO_ENV=development            # development | testing | production
DJANGO_DEBUG=True                 # True in dev, False everywhere else
# Generate with: python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
DJANGO_SECRET_KEY=
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1

# ============================================================================
# LOGGING  (all environments — optional)
# ============================================================================
# LOG_LEVEL sets the app logger; DJANGO_LOG_LEVEL overrides just the framework
# logger and inherits LOG_LEVEL when unset.
# LOG_LEVEL=INFO                  # DEBUG in dev, INFO elsewhere
# DJANGO_LOG_LEVEL=INFO

# ============================================================================
# HTTPS & SECURITY  (testing + production only — defaults are correct in dev)
# ============================================================================
SECURE_SSL_REDIRECT=False         # True in production (Cloud Run terminates TLS)
SESSION_COOKIE_SECURE=False       # True in production
CSRF_COOKIE_SECURE=False          # True in production
SESSION_COOKIE_SAMESITE=Lax       # Lax everywhere — Strict breaks Google OAuth
CSRF_COOKIE_HTTPONLY=True
CSRF_COOKIE_SAMESITE=Lax          # Strict in production
CSRF_TRUSTED_ORIGINS=             # Comma-separated http(s):// origins for non-localhost hosts
# Reverse proxies in front of the app. The rate limiter reads the client IP this
# many hops from the RIGHT of X-Forwarded-For (a proxy APPENDS what it saw, so
# everything further left is client-supplied). Cloud Run and a single nginx are
# both 1; use 0 when the app is reached directly.
TRUSTED_PROXY_COUNT=1
# Optional. The rate limiter is cache-backed and the default LocMemCache is
# per-process, so N Gunicorn workers multiply the effective limit by N. Point
# this at the Redis that Celery already uses to make the limit real.
CACHE_URL=                        # e.g. redis://redis:6379/1

# ============================================================================
# DATABASE  (all environments)
# ============================================================================
# POSTGRES_HOST + POSTGRES_PORT are injected by docker-compose.yml (POSTGRES_HOST=db).
# On Cloud Run, use DATABASE_URL with the Unix-socket query instead:
#   postgres://user:pass@/dbname?host=/cloudsql/PROJECT:REGION:INSTANCE
POSTGRES_DB=fiveaday_db
POSTGRES_USER=fiveaday_user
POSTGRES_PASSWORD=                # openssl rand -base64 32
# DATABASE_URL=

# ============================================================================
# AUTHENTICATION  (development only — testing/production use Teacher login)
# ============================================================================
# In development, /login/ matches against these two values directly and
# get-or-creates a Django superuser with username=LOGIN_USERNAME so /admin/
# keeps working. Omit both in testing/production: Teachers authenticate via
# auth.User (email + password) seeded by TEACHER_SEED_* below.
LOGIN_USERNAME=
LOGIN_PASSWORD=

# ============================================================================
# EMAIL  (all environments — Gmail SMTP + App Password)
# ============================================================================
EMAIL_HOST_USER=                  # your-academy@gmail.com
EMAIL_SECRET=                     # 16-char Gmail App Password
SUPPORT_EMAIL=                    # where support tickets are sent
EMAIL_TEST_1=                     # dev/QA test recipient 1
EMAIL_TEST_2=                     # dev/QA test recipient 2

# ============================================================================
# GOOGLE OAUTH  (optional — recommended in production)
# ============================================================================
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=              # http(s)://YOUR_HOST/auth/google/callback/

# ============================================================================
# TEACHER SEEDING  (testing + production only — read by `manage.py seed_teachers`)
# ============================================================================
# Numbered blocks (N starts at 1, iteration stops at the first missing
# FIRST_NAME). FIRST_NAME / LAST_NAME / EMAIL are required; PHONE / ADMIN /
# PASSWORD are optional. Omit PASSWORD to make the teacher activate via the
# password-reset email (Gmail SMTP must work).
TEACHER_SEED_1_FIRST_NAME=
TEACHER_SEED_1_LAST_NAME=
TEACHER_SEED_1_EMAIL=
TEACHER_SEED_1_PHONE=
TEACHER_SEED_1_ADMIN=True
TEACHER_SEED_1_PASSWORD=

# ============================================================================
# ACADEMY BUSINESS INFO  (prefilled in payment-reminder email forms)
# ============================================================================
ACADEMY_IBAN=
ACADEMY_IBAN_HOLDER=
ACADEMY_PHONE=

# ============================================================================
# GOOGLE SHEETS EXPORT  (v1.2 — optional, all environments)
# ============================================================================
# Both a credential and a spreadsheet id must be set for the integration to
# activate. Otherwise /api/sheets/export/ returns 503 and the management
# command exits with a clear error.
#
# Provide the service-account creds either inline (recommended for Cloud Run
# + Secret Manager) or as a filesystem path:
GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON=
GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE=
GOOGLE_SHEETS_SPREADSHEET_ID=       # doc ID from the sheet's URL

# ============================================================================
# TWILIO SMS  (v1.8 — optional, all environments)
# ============================================================================
# All three required for SmsService.is_configured() to return True. Only
# parents with sms_opt_in=True receive SMS — the campaign is strictly opt-in.
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=                 # E.164 format, e.g. +34600111222

# ============================================================================
# STRIPE  (v1.11 — optional, all environments)
# ============================================================================
# STRIPE_SECRET_KEY toggles the parent-portal "Pagar online" button.
# STRIPE_WEBHOOK_SECRET is REQUIRED in production: when unset, the webhook
# view skips signature verification and any HTTP client could mark payments
# as paid. Use test-mode keys in dev/testing (sk_test_… / whsec_…).
STRIPE_SECRET_KEY=
STRIPE_PUBLISHABLE_KEY=
STRIPE_WEBHOOK_SECRET=

# ============================================================================
# MONITORING  (v1.16 — optional, production mainly)
# ============================================================================
# Shared secret for the /health/?deep=1 row-count fingerprint. /health/ is
# public, so counts are only returned to a caller sending a matching
# X-Probe-Token header. Leave unset and the deep probe still reports database
# connectivity and migration state — just not the counts. Deploy tooling uses
# the counts to prove a release did not land on the wrong database.
HEALTH_PROBE_TOKEN=
```

A few keys are intentionally absent from the template:

- **`APP_VERSION`** — derived from `pyproject.toml`. Setting it as an env var silently overrides the runtime value, so don't.
- **`POSTGRES_HOST` / `POSTGRES_PORT`** — `docker-compose.yml` injects `POSTGRES_HOST=db` and `5432` is the Postgres default. Only set them if running outside Docker against a non-default Postgres.
- **`CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND`** — Docker compose injects `redis://redis:6379/0` for the worker/beat containers. On Cloud Run, leaving them unset triggers Celery eager mode automatically.

### Make Commands

Run `make` or `make help` for the full list. Key commands:

| Command | Description |
|---------|-------------|
| **Setup & Build** | |
| `make setup` | Create `.env` (copies `.env.development` if present, else an empty file) |
| `make build` | Build the Docker images |
| **Lifecycle** | |
| `make up` | Start all services (detached) |
| `make down` | Stop and remove containers |
| `make dev` / `make dev BUILD=1` | Start in foreground (logs visible); optionally build first |
| `make rebuild` / `make rebuild SERVICE=web` | Full rebuild without cache + start |
| `make restart` / `make stop` / `make start` | Lifecycle for one or all services (`SERVICE=x`) |
| **Monitoring** | |
| `make logs` / `make logs SERVICE=web` | Tail logs |
| `make ps` / `make stats` | Show running services / resource usage |
| `make health` | Full health check (services + Django + DB + `/health/`) |
| `make url` | Print access URLs |
| **Django** | |
| `make shell` / `make bash` | Django shell / bash in the web container |
| `make migrate` | Apply migrations |
| `make makemigrations` | Create migrations (all 4 apps) |
| `make createsuperuser` | Create Django superuser |
| `make collectstatic` | Collect static files |
| `make check` / `make check-deploy` | Django system checks / deployment checklist |
| **Database** | |
| `make dbshell` | PostgreSQL shell |
| `make backup` | Dump DB to `backups/` |
| `make restore FILE=backups/X.sql` | Restore from a SQL dump |
| `make reset-db` | Drop and recreate the database (destructive — y/N prompt) |
| **Testing** | |
| `make test` | All tests in Docker against PostgreSQL with coverage |
| `make test unit` / `make test integration` | Only that suite |
| `make test coverage` | All tests with HTML coverage report (`htmlcov/`) |
| `make test K=payment` | Filter by keyword |
| `make test ARGS='--lf'` | Pass raw pytest flags through |
| `make test-cov-gate` | Coverage gate — fails if coverage drops below 75% (used by pre-commit) |
| **Payments** | |
| `make generate-payments` / `make generate-payments-dry` | Generate the current month / preview only |
| **Celery** | |
| `make celery-logs` / `make celery-restart` | Tail or restart worker + beat |
| `make celery-status` / `make celery-test-task` | Inspect active tasks / queue a debug task |
| **Versioning** | |
| `make version` | Show current pyproject + README badge values, warn on drift |
| `make version 1.15.0` | Update `pyproject.toml`, `settings.py`, README badge, regenerate `uv.lock` (with y/N confirmation) |
| **Admin ops** | |
| `manage.py reset_two_factor <email>` | Wipe 2FA secret + backup codes for a locked-out admin (v1.13) |
| `manage.py export_to_sheets [--students] [--payments]` | Push snapshots to Google Sheets (v1.2) |
| `manage.py generate_payments [--month M --year Y]` | Create monthly / quarterly payment rows for active enrollments |
| `manage.py seed_teachers` | Idempotently seed Teacher rows from `TEACHER_SEED_<N>_*` env vars |
| `manage.py seed_testdata [--reset]` | Populate the QA stack with fake students, parents and payments |
| `manage.py send_email --template X [--test]` | Send one email template |
| `manage.py test_all_emails [--only X,Y]` | Render/send every email template for review |
| **Beat-task wrappers** (production has no Beat process — Cloud Scheduler runs these as Cloud Run Jobs) | |
| `manage.py generate_payments` | Monthly/quarterly payment rows (Beat: 1st, 06:00) |
| `manage.py materialize_recurring_expenses [--daily]` | Recurring expenses — monthly pass, or `--daily` for the weekly/yearly pass (Beat: 1st 06:30 / daily 06:15) |
| `manage.py send_birthday_emails` | Birthday emails (Beat: daily 08:00) |
| `manage.py send_payment_reminders` | Overdue-payment reminders (Beat: Mondays 09:00) |
| `manage.py send_monthly_report` | Admin monthly report (Beat: 28th, 20:00) |
| `manage.py send_due_fun_friday_emails` | Drain due `FunFridayScheduledSend` rows (Beat: daily 14:30) |
| `manage.py cleanup_backlog_tasks` | Delete QA backlog tasks done > 30 days ago (Beat: daily 07:00) |
| **Developer Tooling** | |
| `make sync` | Install all deps (including dev) via uv |
| `make lint` / `make lint FIX=1` | Run Ruff linter (optionally auto-fix) |
| `make format` / `make format DRY=1` | Run Ruff formatter (DRY=1 = check only) |
| `make mypy` | Run mypy type checker |
| `make bandit` / `make audit` | Bandit security linter / `pip-audit` for dependency CVEs |
| `make coverage-badge` | Regenerate `coverage.svg` from the latest test run |
| `make pre-commit-install` | Install the git pre-commit hook |
| `make pc-run` | Run pre-commit on all files; on clean pass, offer to auto-bump patch version; auto-stages regenerated `uv.lock` |
| **Remote** | |
| `make connect-testing` | SSH into the GCP testing VM (auto-login if needed) |
| **Cleanup** | |
| `make clean` | Remove stopped containers + system prune |
| `make clean-all` | Remove everything including volumes (y/N prompt) |

### Environment Configuration

The project supports three environments, controlled by `DJANGO_ENV` and `DJANGO_DEBUG`:

| Environment | `DJANGO_ENV` | `DJANGO_DEBUG` | Database | Static Files | Use Case |
|------------|-------------|---------------|----------|-------------|----------|
| **Production** | `production` | `false` | PostgreSQL (Cloud SQL) | WhiteNoise + collectstatic | Live deployment |
| **Testing** | (via settings_test.py) | `false` | PostgreSQL (Docker) | Simple storage | `make test` |
| **Development** | `development` | `true` | PostgreSQL (Docker) | Django dev server | Local coding |


> **Defaults are production-safe**: `DJANGO_DEBUG` defaults to `false` and `DJANGO_ENV` defaults to `development`. In production, always set `DJANGO_ENV=production` and ensure `DJANGO_SECRET_KEY` is a strong random value.

The database is **always PostgreSQL** — in Docker development, in tests, and in production. Tests run against the same Docker PostgreSQL container to ensure realistic behavior.

### Environment Variables Reference

The table below describes every variable in the [.env template](#env-template) above, plus a few advanced overrides not included in the template. See the template for the full `.env` structure.

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| **Django core** | | | |
| `DJANGO_ENV` | Environment: `development` / `production` / `testing` | No | `development` |
| `DJANGO_DEBUG` | Debug mode: `true` / `false` | No | `false` |
| `DJANGO_SECRET_KEY` | Secret key | **Yes in production** | dev fallback |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hosts | No | `localhost,127.0.0.1` |
| `SECURE_SSL_REDIRECT` | Force HTTPS redirects | No | `True` when `DEBUG=False` |
| **Database** | | | |
| `DATABASE_URL` | Full URL (Cloud deployments) | No | — |
| `POSTGRES_DB` | Database name | No | `fiveaday_db` |
| `POSTGRES_USER` | Database user | No | `fiveaday_user` |
| `POSTGRES_PASSWORD` | Database password | **Yes** | — |
| `POSTGRES_HOST` | Database host | No | `localhost` (compose injects `db`) |
| `POSTGRES_PORT` | Database port | No | `5432` |
| **Email** | | | |
| `EMAIL_HOST_USER` | Gmail address | For email features | — |
| `EMAIL_SECRET` | Gmail app password | For email features | — |
| `SUPPORT_EMAIL` | Support ticket recipient | No | — |
| `EMAIL_TEST_1` / `EMAIL_TEST_2` | Test email recipients | No | — |
| **Auth** | | | |
| `LOGIN_USERNAME` | Dev-only basic-auth username (compared by the login view when `DJANGO_ENV=development`). Ignored in testing/production. | **Yes in dev** | — (login refused if missing) |
| `LOGIN_PASSWORD` | Dev-only basic-auth password. Ignored in testing/production — Teachers log in via `auth.User` (seed them with `TEACHER_SEED_*`). | **Yes in dev** | — (login refused if missing) |
| `GOOGLE_CLIENT_ID` | OAuth client ID | For Google login | — |
| `GOOGLE_CLIENT_SECRET` | OAuth client secret | For Google login | — |
| `GOOGLE_REDIRECT_URI` | OAuth callback URL | For Google login | auto-detected |
| `GOOGLE_ALLOWED_EMAIL` | Restrict Google login to one email | No | `EMAIL_HOST_USER` |
| **Teacher seeding** (read by `manage.py seed_teachers`; runs automatically on container start in testing/production) | | | |
| `TEACHER_SEED_<N>_FIRST_NAME` | First name for the Nth teacher block (N from 1; iteration stops at first missing FIRST_NAME) | For testing/prod | — |
| `TEACHER_SEED_<N>_LAST_NAME` | Last name | For testing/prod | — |
| `TEACHER_SEED_<N>_EMAIL` | Email — used as the Django `User.username` and login credential | For testing/prod | — |
| `TEACHER_SEED_<N>_PHONE` | Phone | No | — |
| `TEACHER_SEED_<N>_ADMIN` | `True` / `False` — controls dashboard access tier and mirrors onto `is_staff` + `is_superuser` | No | `False` |
| `TEACHER_SEED_<N>_PASSWORD` | Initial password. Omit to force activation via `/password-reset/`. Re-running seed never overwrites a password an admin later changed. | No | unusable password |
| **Celery / Redis** | | | |
| `CELERY_BROKER_URL` | Redis URL for Celery | No | eager mode (tasks run inline) |
| `CELERY_RESULT_BACKEND` | Redis URL for results | No | same as broker |
| **Academy business info** (prefills payment-reminder email forms) | | | |
| `ACADEMY_IBAN` | Bank account for payment reminders | No | — |
| `ACADEMY_IBAN_HOLDER` | IBAN account holder | No | — |
| `ACADEMY_PHONE` | Phone for Bizum payments | No | — |
| **Google Sheets export (v1.2)** — optional, dormant until configured | | | |
| `GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON` | Inline service-account JSON (recommended for Cloud Run + Secret Manager) | No | — |
| `GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE` | Filesystem path to a service-account JSON file (alternative to inline) | No | — |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | Target spreadsheet doc ID; service account must have Editor access | No | — |
| **Twilio SMS (v1.8)** — optional, opt-in per parent | | | |
| `TWILIO_ACCOUNT_SID` | Twilio Account SID | No | — |
| `TWILIO_AUTH_TOKEN` | Twilio Auth Token | No | — |
| `TWILIO_FROM_NUMBER` | E.164-format sender (e.g. `+34600111222`) | No | — |
| **Stripe (v1.11)** — optional, gates the parent-portal "Pagar online" button | | | |
| `STRIPE_SECRET_KEY` | Stripe Secret Key (`sk_test_…` in dev/testing, `sk_live_…` in prod) | No | — |
| `STRIPE_PUBLISHABLE_KEY` | Stripe Publishable Key (`pk_…`) — client-side reference only | No | — |
| `STRIPE_WEBHOOK_SECRET` | Webhook signing secret (`whsec_…`) — **REQUIRED in prod**; when unset the webhook view rejects all events | For prod Stripe | — |
| **Rate limiting (v1.10)** | | | |
| `RATELIMIT_ENABLE` | Set to `False` to bypass the login/portal rate limiter (used in tests; leave unset in real envs) | No | `True` |
| **Monitoring (v1.16)** | | | |
| `HEALTH_PROBE_TOKEN` | Shared secret for the `/health/?deep=1` row-count fingerprint, sent as `X-Probe-Token`. Unset means the deep probe still reports DB connectivity and migration state, but no counts | No | — (counts disabled) |
| **Logging / misc** | | | |
| `LOG_LEVEL` | App log level | No | `DEBUG` in dev, `INFO` in prod |
| `DJANGO_LOG_LEVEL` | Django framework log level | No | inherits `LOG_LEVEL` |
| `APP_VERSION` | Version string override | No | from `settings.py` default |
| `DJANGO_SUPERUSER_EMAIL` | Email given to the auto-created dev superuser, and last fallback for the Google-login allow-list | No | `<LOGIN_USERNAME>@local.dev` |
| **Security headers & cookies** (advanced overrides — all applied only when `DEBUG=False`) | | | |
| `SESSION_COOKIE_SECURE` | HTTPS-only session cookie | No | `True` |
| `CSRF_COOKIE_SECURE` | HTTPS-only CSRF cookie | No | `True` |
| `SESSION_COOKIE_HTTPONLY` | Block JS access to the session cookie | No | `True` |
| `CSRF_COOKIE_HTTPONLY` | Block JS access to the CSRF cookie — **why AJAX must read the token from the hidden input, not the cookie** | No | `True` when `DEBUG=False` |
| `SESSION_COOKIE_SAMESITE` | Session-cookie SameSite policy. Must stay `Lax` — see below | No | `Lax` |
| `CSRF_COOKIE_SAMESITE` | CSRF-cookie SameSite policy | No | `Strict` (`Lax` in dev) |
| `SESSION_SAVE_EVERY_REQUEST` | Refresh the session on every request, making `SESSION_COOKIE_AGE` an inactivity timeout | No | `True` |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated origins allowed to POST (needed behind a proxy or a custom domain) | No | — |
| `TRUSTED_PROXY_COUNT` | Reverse proxies in front of the app. The rate limiter reads the client IP this many hops from the **right** of `X-Forwarded-For`, because a proxy appends what it saw — anything further left is client-supplied. `0` ignores the header entirely | No | `1` |
| `CACHE_URL` | Redis URL for the cache backing the rate limiter. Without it Django uses `LocMemCache`, which is **per-process**, so N Gunicorn workers multiply the effective limit by N | No | — (LocMem) |
| `SECURE_HSTS_SECONDS` | HSTS max-age | No | `31536000` (1 y) |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` / `SECURE_HSTS_PRELOAD` | HSTS scope | No | `True` |
| `SECURE_CONTENT_TYPE_NOSNIFF` | `X-Content-Type-Options: nosniff` | No | `True` |
| `SECURE_BROWSER_XSS_FILTER` | Legacy XSS filter header | No | `True` |
| `X_FRAME_OPTIONS` | Clickjacking protection | No | `DENY` |
| **Test suite only** (read by `project/settings_test.py`) | | | |
| `TEST_DB_HOST` | Postgres host for the test database — `make test` sets `db` inside Docker | No | `localhost` |
| `TEST_DB_ENGINE` | Set to `sqlite` for a Docker-free local fallback. Not for CI or normal use — always prefer `make test` against Postgres | No | postgres |
| `SESSION_COOKIE_AGE` | Session duration (seconds) — sessions expire after this much inactivity, since `SESSION_SAVE_EVERY_REQUEST` is on | No | `21600` (6 h) |

### App Versioning

The app version is defined in **four places** and `make version x.y.z` updates all four together:

1. **`pyproject.toml`** — `version = "x.y.z"` (package metadata)
2. **`project/project/settings.py`** — `APP_VERSION = os.getenv("APP_VERSION", "x.y.z")` (runtime fallback)
3. **`README.md`** — the header version badge URL
4. **`uv.lock`** — regenerated via `uv lock --quiet` so the lockfile's own `[[package]]` entry stays in sync

`make version x.y.z` prompts `Version A will become the new version B, are you sure?` before writing. Running `make version` with no argument prints both the pyproject and README badge values side-by-side and warns if they've drifted. `make pc-run` also auto-bumps the patch digit on successful pre-commit (y/N prompt) and stages the regenerated `uv.lock` automatically.

The version appears in:
- `/health/` endpoint response
- Support ticket emails
- Can be overridden at runtime via the `APP_VERSION` environment variable (do **not** leave a legacy value like `0.x.y` in `.env` — remove the line so the default in `settings.py` takes effect)

---

## Project Structure & Architecture

### Architecture Overview

```mermaid
graph TB
    Browser[Browser] --> Django[Django / Gunicorn :8000]
    Django --> PG[(PostgreSQL :5432)]
    Django --> SMTP[Gmail SMTP]
    Django --> OAuth[Google OAuth]

    subgraph "Django Apps"
        Core["<b>core</b><br/>Dashboard, Auth<br/>Schedule, Utilities<br/><i>8 models, 3 services</i>"]
        Students["<b>students</b><br/>Student, Parent<br/>Teacher, Group<br/><i>6 models</i>"]
        Billing["<b>billing</b><br/>Payment, Enrollment<br/>Pricing, Exports<br/><i>5 models, 6 services</i>"]
        Comms["<b>comms</b><br/>Email Service<br/>Tasks, Commands<br/><i>0 models, 3 services</i>"]
    end

    Core --> Students
    Core --> Billing
    Core --> Comms
    Billing --> Students
    Comms --> Students
    Comms --> Billing
```

### App Dependency Flow

```mermaid
graph LR
    students["<b>students</b><br/>(foundation — no dependencies)"] --> billing["<b>billing</b><br/>(FK to Student, Parent)"]
    students --> core["<b>core</b><br/>(FK to Student, Group)"]
    students --> comms["<b>comms</b><br/>(email recipients)"]
    billing --> comms
```

### Directory Layout

```text
five-a-day/
├── project/
│   ├── project/                  Django settings module
│   │   ├── settings.py           Main settings (env-driven)
│   │   ├── settings_test.py      Test overrides (PostgreSQL default, SQLite fallback)
│   │   ├── urls.py               Root URL conf → includes 4 app URL files
│   │   ├── celery.py             Celery app + Beat schedule (8 periodic tasks)
│   │   └── wsgi.py / asgi.py
│   │
│   ├── core/                     Dashboard, Auth, Schedule, Utilities, Cross-cutting
│   │   ├── models.py             TodoItem, HistoryLog, FunFridayAttendance, ScheduleSlot,
│   │   │                         BacklogTask, QAConfiguration, FunFridayScheduledSend (v1.14.2)
│   │   ├── audit_models.py       AuditLog (v1.10 — immutable per-model change trail)
│   │   ├── audit_signals.py      Signal receivers + AuditActorMiddleware (contextvar-based actor)
│   │   ├── rate_limit.py         Cache-backed IP rate limiter (v1.10)
│   │   ├── log_safe.py           safe_log() — CR/LF-stripping log sanitizer (v1.14.4)
│   │   ├── views/                22 view modules — auth, password_reset, dashboard,
│   │   │                         students, parents, payments, management, app_forms,
│   │   │                         schedule, fun_friday_attendance, todos, support,
│   │   │                         errors, testing_tools, waiting_list (v1.1), sheets (v1.2),
│   │   │                         expenses (v1.5), reports (v1.7), parent_portal (v1.9),
│   │   │                         stripe_views (v1.11), pwa (v1.12), two_factor (v1.13)
│   │   ├── services/             3 modules — analytics_service (v1.7),
│   │   │                         google_sheets_service (v1.2), two_factor_service (v1.13)
│   │   ├── constants.py          DIAS_ES, MESES_ES, SCHEDULED_APPS
│   │   ├── middleware.py         SimpleAuthMiddleware + QAErrorEmailMiddleware
│   │   │                         + NoHtmlCacheMiddleware
│   │   ├── decorators.py         qa_access_required (testing env gate)
│   │   ├── context_processors.py Notifications + is_admin_user / is_non_admin_teacher flags
│   │   ├── transactions.py       Optimised queryset builders with stable ordering
│   │   ├── templates/            All HTML templates — base, pages, emails/, parent_portal/,
│   │   │                         two_factor/ (v1.13), plus expenses/reports/waiting_list
│   │   │                         and waiting_list_create (v1.15)
│   │   ├── static/               CSS (app.css, theme.css, email.css, admin_custom.css)
│   │   │                         + JS (17 modules) + images
│   │   └── management/commands/  seed_teachers, seed_testdata, export_to_sheets (v1.2),
│   │                             reset_two_factor (v1.13), cleanup_backlog_tasks (v1.14.2),
│   │                             prune_audit_log (v1.15)
│   │
│   ├── students/                 People Management
│   │   ├── models.py             Student, Parent, StudentParent, Teacher, Group.
│   │   │                         v1.1: Student.is_waiting/waiting_since + Group.max_students.
│   │   │                         v1.8: Parent.sms_opt_in. v1.13: Teacher.two_factor_*.
│   │   │                         v1.15: nullable birth_date/group + course, observations,
│   │   │                         waiting_contact_name, waiting_contact_phone
│   │   ├── parent_portal_models.py  ParentSessionToken (v1.9, magic-link + SELECT FOR UPDATE)
│   │   ├── forms.py              StudentForm, WaitingListForm (v1.15), ParentForm, ParentFormSet
│   │   ├── admin.py              Custom admin with inlines + group capacity columns
│   │   ├── urls.py               15 URL patterns (v1.15: waiting_list_create)
│   │   └── migrations/           8 migrations (through 0008_student_course_...)
│   │
│   ├── billing/                  Financial Management
│   │   ├── models.py             SiteConfiguration (v1.13: returning_student_enrollment_discount),
│   │   │                         EnrollmentType, Enrollment, Payment (v1.11: stripe_session_id,
│   │   │                         stripe_payment_intent), Expense (v1.5)
│   │   ├── forms.py              EnrollmentForm (delegates to service)
│   │   ├── constants.py          Pricing seeds, choice tuples
│   │   ├── services/             6 modules — enrollment_service (v1.13: returning-student
│   │   │                         detection), payment_service, pricing_service,
│   │   │                         expense_service (v1.5), pdf_service (v1.3 — reportlab),
│   │   │                         stripe_service (v1.11 — httpx, no SDK dep)
│   │   ├── tasks.py              generate_monthly_payments_task, materialize_recurring_expenses_task
│   │   ├── exports.py            Excel/CSV builders
│   │   ├── admin.py              Payment + Enrollment + Expense admin
│   │   ├── urls.py               24 URL patterns (v1.15: student_payments_pdf)
│   │   └── management/commands/  generate_payments, materialize_recurring_expenses (v1.14.2)
│   │
│   ├── comms/                    Communications
│   │   ├── services/             email_service (EmailService singleton),
│   │   │                         email_functions (12 convenience helpers),
│   │   │                         sms_service (v1.8 — Twilio, lazy import)
│   │   ├── tasks.py              12 Celery tasks — welcome, birthday (all parents, v1.13
│   │   │                         localdate), payment reminders (email + SMS dedup),
│   │   │                         monthly report, magic link (v1.9), payment receipt (v1.11),
│   │   │                         Fun Friday drain (v1.14.2)
│   │   ├── urls.py               11 URL patterns
│   │   └── management/commands/  send_email, test_all_emails, plus 4 Beat-task wrappers
│   │                             (v1.14.2 — birthday, reminders, report, Fun Friday drain)
│   │
│   ├── tests/                    pytest suite (1,214 tests, 95.24 % coverage) — unit/ + integration/
│   ├── templates/registration/   Password-reset templates (form, done, confirm, complete + email body)
│   ├── templates/admin/          Django admin overrides (branded theme)
│   └── conftest.py               Shared fixtures (models + authenticated_client)
│
├── .github/                      CI/CD — see docs/GITHUB.md
│   ├── workflows/
│   │   ├── ci.yml                     Lint + typecheck + tests + Docker build + CVE scan on every push/PR
│   │   ├── auto-merge.yml             Hourly development → testing merge + PR to main
│   │   ├── codeql.yml                 Weekly Python security scan
│   │   ├── notify-production.yml      Email on push to main
│   │   ├── dependabot-auto-merge.yml  Auto-merge Dependabot minor/patch PRs
│   │   ├── dependency-review.yml      Block PRs introducing HIGH/CRITICAL CVEs
│   │   └── scorecard.yml              OSSF Scorecard supply-chain security (weekly)
│   ├── dependabot.yml            Weekly dependency updates
│   └── CODEOWNERS                Auto-request reviews from owner accounts
│
├── docs/
│   ├── GITHUB.md                 Full CI/CD + branch protection reference
│   ├── HTTPS.md                  HTTPS setup (Docker Nginx + Cloud Run)
│   ├── UV.md                     UV dependency management guide
│   ├── CELERY.md                 Celery worker/beat reference
│   └── TODO.md                   Open tasks
│
├── scripts/                      Dev helpers + backup_retention.sh (Cloud SQL tiers)
├── backups/                      DB dumps from `make backup` (gitignored)
│
├── Dockerfile                    Multi-stage build (builder + runtime)
├── docker-compose.yml            PostgreSQL + Redis + Django + Celery worker + beat
├── docker-compose.testing.yml    QA override (Gunicorn, DEBUG=False)
├── Makefile                      48 targets (`make help`)
├── pyproject.toml                Dependencies (uv-managed) + tool config
├── uv.lock                       Reproducible dependency lock
├── entrypoint.sh                 Docker entrypoint (migrate, collectstatic, start)
├── .env.development /            Gitignored env files (`.env*` matches all variants).
│   .env.testing /                One of them is renamed to `.env` before bringing the
│   .env.production               stack up — that's the file settings.py loads.
├── CLAUDE.md                     AI development context (project rules)
├── DEPLOYMENT.md                 GCP deployment guide (all 3 environments)
└── README.md                     This file
```

### App: core

Dashboard, authentication, scheduling, and shared utilities. Owns all views and templates.

| Component | Details |
|-----------|---------|
| **Models** | 8 — TodoItem, HistoryLog (1000-entry cap), FunFridayAttendance, FunFridayScheduledSend, ScheduleSlot, BacklogTask (QA), QAConfiguration (QA), plus AuditLog in `audit_models.py` |
| **Views** | 22 modules: auth, password_reset, dashboard, students, parents, payments, management, app_forms, schedule, fun_friday_attendance, todos, support, errors, testing_tools, waiting_list, sheets, expenses, reports, parent_portal, stripe_views, pwa, two_factor |
| **Services** | 3 — analytics_service, google_sheets_service, two_factor_service |
| **Middleware** | 4 — NoHtmlCacheMiddleware (no-cache on dynamic HTML), QAErrorEmailMiddleware, SimpleAuthMiddleware (session auth public allow-list incl. `/password-reset/` + non-admin teacher URL-name whitelist), AuditActorMiddleware |
| **Templates** | base.html (layout), 23 page templates, 18 email templates + `base_email.html` (common violet style + dark-mode support), error pages, plus `templates/registration/` for the password-reset flow |
| **Static** | 4 CSS files (app.css, theme.css, email.css, admin_custom.css), 17 JS modules, images |
| **Commands** | seed_teachers (Teacher + auth.User from env vars), seed_testdata, export_to_sheets, reset_two_factor, cleanup_backlog_tasks, prune_audit_log (v1.15) |
| **URLs** | 46 patterns: dashboard, auth, password reset, schedule, todos, support, QA (incl. backlog export), PWA, 2FA, parent portal |

See [core/README.md](project/core/README.md) for details.

### App: students

People management — the foundation app with no external dependencies.

| Component | Details |
|-----------|---------|
| **Models** | 6 — Student (age calc, withdrawal + waiting-list tracking; v1.15 nullable `birth_date`/`group` plus `course`, `observations`, `waiting_contact_name`, `waiting_contact_phone`), Parent (DNI unique, `sms_opt_in`), Teacher (optional `auth.User` link for login, `two_factor_*`), Group (`max_students` capacity), StudentParent (M2M through), plus ParentSessionToken in `parent_portal_models.py` |
| **Forms** | StudentForm (birth_date validation), WaitingListForm (v1.15 — name + phone only), ParentForm (DNI validation; uniqueness deferred to the view so a repeat DNI reuses the existing parent), ParentFormSet |
| **Admin** | StudentAdmin with StudentParentInline, ParentAdmin with ParentStudentInline, group capacity columns |
| **URLs** | 15 patterns: CRUD + search + fun friday attendance + waiting list (incl. the v1.15 short create form) |
| **Auth integration** | `Teacher.ensure_user(password=...)` get-or-creates the linked Django user; `post_save` signal mirrors admin / email / name onto `auth.User` |

See [students/README.md](project/students/README.md) for details.

### App: billing

Financial management with a dedicated service layer.

| Component | Details |
|-----------|---------|
| **Models** | 5 — SiteConfiguration (singleton pricing), EnrollmentType (plan types), Enrollment (discount flags), Payment (overdue detection, Stripe ids), Expense (three recurring cadences) |
| **Services** | 6 — EnrollmentService (creation + discounts + returning-student detection), PaymentService (generation + calculations; v1.15 quarterly amounts now carry sibling / language-cheque / June discounts), PricingService (centralized config access), ExpenseService, PdfService (reportlab; v1.15 per-student payment history), StripeService (httpx, no SDK dependency) |
| **Constants** | Pricing seeds, ENROLLMENT_TYPE_CHOICES, SCHEDULE_TYPE_CHOICES, PAYMENT_METHOD_CHOICES, etc. |
| **Exports** | build_database_workbook() → multi-sheet .xlsx |
| **Celery tasks** | 3 — generate_monthly_payments_task, materialize_recurring_expenses_task, materialize_recurring_expenses_daily_task |
| **Commands** | `generate_payments --month X --year Y [--dry-run]`, `materialize_recurring_expenses [--daily]` |
| **URLs** | 24 patterns: payment CRUD, enrollment API, expenses, reports, exports, Stripe, per-student payment-history PDF |

See [billing/README.md](project/billing/README.md) for details.

### App: comms

Email and SMS communications — no database models, pure service layer.

| Component | Details |
|-----------|---------|
| **EmailService** | Generic HTML email sender with inline images and attachments |
| **SmsService** | Twilio SMS sender (lazy import, opt-in only — `Parent.sms_opt_in`) |
| **Email functions** | 12 convenience functions (birthday, welcome, enrollment, payment reminder, receipts, tax cert, fun friday, vacation closure, monthly report) |
| **Celery tasks** | 12 tasks with retry logic: welcome, birthday (single + batch), payment reminders (email + SMS dedup), monthly report, generic, enrollment confirmation, parent magic link, payment receipt, Fun Friday (batch + due-drain) |
| **Commands** | `send_email --template X [--test]`, `test_all_emails [--only X,Y]`, plus 4 Beat-task wrappers (send_birthday_emails, send_payment_reminders, send_monthly_report, send_due_fun_friday_emails) |
| **URLs** | 11 patterns: all email app form views |

See [comms/README.md](project/comms/README.md) for details.

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| Views stay in core | Models split across apps, but all views in `core/views/` avoids template/URL fragmentation. Each app's `urls.py` imports from core. |
| Service layer in billing | Business logic (pricing, discounts, payment generation) extracted from forms/views into testable services. |
| SiteConfiguration singleton | All pricing editable from UI. Auto-creates with defaults. No hardcoded prices in views. |
| Two-mode auth | Dev compares against `LOGIN_USERNAME`/`LOGIN_PASSWORD` env vars; testing/production authenticates Teachers via the linked `auth.User`. SimpleAuthMiddleware adds a non-admin Teacher whitelist on top so role-based gating is enforced even on direct URL access. |
| Tailwind CDN | Zero build tools. All utilities available instantly. Custom violet palette in config block. |
| PostgreSQL everywhere | Same database engine in development, testing, and production. Avoids SQLite behavioral differences. |

---

## Features by View

### Home (Dashboard)

The main landing page. Shows real-time operational data for the current month.

- **Pending payments card** — count + student names with amounts. Click count to expand modal with full student list and individual amounts.
- **Birthdays card** — monthly count with today's birthdays highlighted by name.
- **Upcoming events** — Fun Fridays and scheduled email sends for the rest of the month, linked to their form views.
- **Monthly revenue** — expected total (all due this month) vs completed total (paid this month), with payment count.
- **Todo list** — create tasks with date selector (today / this week's Friday / custom date picker). Overdue items shown in red. Check to complete (deletes + logs to HistoryLog). Sorted by due date.
- **History dropdown** — lazy-loaded, paginated (20 per page) log of all actions: payments completed, students enrolled, emails sent, config changes.
- **Notification bell** — badge count of today's due tasks + today's scheduled email sends.

### Students

Student management with toolbar, inline actions, and real-time filtering.

- **Student table** — columns: name, group (color badge), enrollment type, Fun Friday status icon. Rows have `data-*` attributes for client-side filtering.
- **Search** — real-time filter by name (client-side, no server round-trip).
- **Sort** — 4-state cycle: date ascending → date descending → name A-Z → name Z-A.
- **Fun Friday toggle** — per-row button. States: green check (registered this week), amber check (this + last week), amber X (only last week), grey X (neither). AJAX POST to `/api/students/{id}/fun-friday/toggle/`.
- **Fun Friday filter** — 3-state cycle: all → not this week → this week only.
- **Type filter** — 4-state cycle: all → children only → adults only → language cheque students.
- **New student dropdown** — choose creation flow: new parent → new student, existing parent → new student, or adult student (no parent).

### Student Create

Multi-step creation form with live price calculator.

- **Parent selection** — either create new (name, DNI, phone, email, IBAN) or search existing parents with pagination (6 per page).
- **Student fields** — first name, last name, birth date (validated: not future), school, allergies, GDPR consent, group selector.
- **Enrollment plan** — dropdown: monthly full-time (2 days/week), monthly part-time (1 day/week), quarterly. Checkboxes: language cheque discount, sibling discount (with sibling search), special/manual price.
- **Live price calculator** — updates as you change plan/discounts. Shows base price, strikethrough, final price, and breakdown text (e.g., "trimestral incl. -5%, -20 cheque").
- **Adult mode** — no parent needed, email/phone on student, fixed adult_group pricing.
- **On submit** — atomic transaction creates: Student → StudentParent link → Enrollment (active) → Payment (enrollment fee, pending) → HistoryLog entry → Celery welcome email task.
- **Success page** — shows student name, enrollment fee amount. Auto-redirects to student list after 4 seconds. Option to "create sibling" (pre-fills same parent).

### Student Detail & Update

- **Detail view** — personal info, linked parents with contact details, enrollment history (all enrollments, active highlighted), payment history, Fun Friday dates with add/remove.
- **Enrollment modality toggle** — switch monthly ↔ quarterly via AJAX.
- **Update view** — same form as create, pre-filled. Saves student changes + finishes old enrollment + creates new enrollment.

### Payments

Payment management with search, filtering, pagination, and quick-complete.

- **Stats bar** — 4 cards: expected total, completed total, pending total, overdue total. All for the current period.
- **Payment table** — columns: student, parent, concept, amount, method, status badge, due date, payment date. Client-side pagination (10 per page).
- **Search** — real-time filter by student name, parent name, concept, or reference number.
- **Status filter** — 4-state cycle: all → pending → completed → overdue.
- **Type filter** — 5-state: all → enrollment → monthly → quarterly → other.
- **Quick complete** — click a pending status badge → dropdown with 3 payment methods (cash / transfer / card) → one click marks as completed with today's date, logs to history.
- **Create payment** — autocomplete student search → autocomplete parent search → validates student-parent relationship → select type, method, amount, dates, concept.
- **Detail view** — read-only display of all payment fields.
- **Export** — CSV download (all payments) and Excel download (full database: students + enrollments + payments as multi-sheet .xlsx).

### Expenses

`/gastos/` (v1.5) — admin only.

- **Expense list** — description, category, amount, date, with monthly totals per category.
- **Create expense** — one-off, or recurring with one of three cadences: **monthly** (`recurring_day` 1-28), **weekly** (`recurring_weekdays`, a CSV of ints 0-6 with Monday=0), or **yearly** (`recurring_day` + `recurring_month`). `Expense.clean()` validates the right fields per cadence.
- **Auto-materialisation** — recurring rows spawn real expense rows: monthly on the 1st (`materialize_recurring`), weekly and yearly daily at 06:15 (`materialize_recurring_for_date`). Both are idempotent, matching on `generated_from` + the exact `expense_date`, so a re-run never double-creates.
- **Delete** — removes the row; generated children keep their `generated_from` link.

### Reports

`/informes/` (v1.7) — admin only.

- **Financial summary** — expected vs collected vs outstanding for the selected month/year.
- **Collection rate** — paid/total ratio over the period.
- **Retention snapshot** — active, withdrawn, and waiting counts.
- **Group utilisation** — per-group occupancy against `Group.max_students`.
- **PDF export** — `reports_pdf` renders the same dashboard as a reportlab document.

### Schedule

Weekly class timetable with drag-and-drop group assignment.

- **Grid** — 5 columns (Mon-Fri) × 3 time rows × 2 sub-columns. Time slots: 16:10-17:30, 17:40-19:00, 19:10-20:30. Friday: 16:00-17:20.
- **Edit mode** — toggle button. In edit mode, click any cell → dropdown to assign a group. Saves via AJAX to `/api/schedule/slot/save/`.
- **Cell display** — group color, group name, teacher first name, student first names.

### Fun Friday

Dedicated attendance management for the weekly Fun Friday event.

- **Student list** — all non-adult active students, grouped by class group.
- **Toggle buttons** — same icon system as student list. AJAX toggles.
- **This week / Last week panels** — lists of registered students for each Friday.
- **Search, sort, filter** — same tools as student list.

### Waiting List

`/lista-espera/` (v1.1) — admin only.

- **Group capacity** — every `Group` has `max_students`; `group_capacity_summary` reports occupancy and free seats.
- **Waiting students** — `Student.is_waiting` + `waiting_since`. Waiting students are excluded from the main student list and shown here in arrival order.
- **Add to waiting list** — from the student-creation flow when the chosen group is full.
- **Assign from waiting list** — moves a waiting student into a group once a seat frees up. This runs the *same* post-enrollment work as a normal creation: the enrollment-fee payment plus `PaymentService.schedule_academic_year_payments`, so the whole academic year is scheduled exactly once.
- **Capacity-freed notification** — `notify_capacity_freed` flags a group that dropped below capacity.

### Apps (Email Tools)

Hub page listing all 10 email communication tools. Each follows a consistent pattern:

1. **Form** — fields specific to the email type (dates, activity description, year, etc.)
2. **Email preview** — collapsible panel showing the rendered email HTML. "Refresh" button fetches live preview with current form data via AJAX.
3. **Test send** — sends to `EMAIL_TEST_1` / `EMAIL_TEST_2` env vars for verification before bulk send.
4. **Send** — iterates over qualifying parent emails, sends individually, counts success/failures, logs to HistoryLog, shows flash messages.

| App | Email Template | Recipients | Trigger |
|-----|---------------|------------|---------|
| Fun Friday | `fun_friday.html` | Parents with active non-adult students | Weekly, manual — persisted as `FunFridayScheduledSend`, sent Monday 14:30 of the event week |
| Payment Reminder | `payment_reminder.html` | Parents with active students | Monthly, manual |
| Vacation Closure | `vacation_closure.html` | All parents | Manual |
| Tax Certificate | `tax_certificate.html` | Parents with completed payments in year | Yearly (April) |
| Monthly Report | `monthly_report.html` | All parents (personalized per parent) | Monthly, manual |
| Birthday | `happy_birthday.html` | Parents of today's birthday students | Daily, manual |
| Receipts (child) | `receipt_quarterly_child.html` | Parents with active children | Quarterly, manual |
| Receipts (adult) | `receipt_adult.html` | Adult students | Monthly, manual |
| Welcome | `welcome_student.html` | Parent of new student | On creation (auto) |
| Enrollment | `enrollment_child.html` / `enrollment_adult.html` | Parent of enrolled student | On enrollment |

### Management

Admin configuration panel with live editing.

- **Pricing config** — all fees and discounts from SiteConfiguration. Toggle edit mode → modify values → save via AJAX. Fields: children/adult enrollment fees, full-time/part-time/adult monthly fees, 8 discount types.
- **Teachers** — create via modal (name, email, phone). Validates unique email. Lists active teachers. Teachers created here are **always non-admin** (`create_teacher` hard-codes `admin=False`) — only seeded teachers (`TEACHER_SEED_<N>_ADMIN=True`) and the superuser are admins, and an existing admin promotes others via `/admin/`.
- **Groups** — create via modal (name, color picker, teacher dropdown). Teacher list populated via AJAX from `/api/teachers/`. Validates unique name.
- **Language cheque API** — `GET /api/students/language-cheque/` returns all students with active language cheque for government reporting.

### Database (All Info)

Paginated read-only tables of all data.

- **Students tab** — sortable by creation date, ID, first name, last name. Paginated (20 per page).
- **Payments tab** — sortable by creation date or student name. Paginated (20 per page).
- **Excel export button** — downloads complete database as `five_a_day_YYYYMMDD.xlsx`.

### Login

Standalone page with custom styling (does not extend base.html). The login view dispatches by environment:

- **Development** — credentials checked against `LOGIN_USERNAME` / `LOGIN_PASSWORD` from the active `.env`. A matching Django superuser is get-or-created so `/admin/` works in the same session.
- **Testing / production** — credentials checked against `auth.User` via `ModelBackend`. Teachers log in with their email + hashed password (seeded by `manage.py seed_teachers` from `TEACHER_SEED_*` env vars). Non-admin Teachers reach a slimmed-down dashboard with the SimpleAuthMiddleware whitelist enforcing URL-level gating.
- **Google OAuth** — optional. Button shown if `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are configured. Validates email matches `GOOGLE_ALLOWED_EMAIL`, get-or-creates a Django superuser, links it to an existing Teacher by email if one exists, and stores Google credentials in session for Gmail/Sheets API access. The same login also grants `/admin/`.
- **"¿Has olvidado tu contraseña?"** — shown in non-dev environments, links into the password-reset flow below.
- **Session** — every successful login goes through `django.contrib.auth.login(...)` (sets `_auth_user_id`) *and* the legacy `session["is_authenticated"]` flag for the middleware. Expires after **6 h of inactivity** (`SESSION_COOKIE_AGE=21600` + `SESSION_SAVE_EVERY_REQUEST=True`).

### Password Reset

Public flow at `/password-reset/...` that lets a teacher recover access without admin intervention.

- **Request page** (`/password-reset/`) — enter email; if it matches a `auth.User`, an HTML email is sent (template at `core/templates/emails/password_reset.html`) with a signed reset link.
- **Sent confirmation** (`/password-reset/sent/`) — generic confirmation that does not disclose whether the email existed.
- **Confirm form** (`/password-reset/confirm/<uidb64>/<token>/`) — new-password form using Django's signed token machinery; rejects expired or replayed links.
- **Complete page** (`/password-reset/complete/`) — confirms the change and links back to login.
- All four URLs are listed in `SimpleAuthMiddleware.PUBLIC_PREFIXES` so a locked-out teacher can reach them. Branded templates live under `project/templates/registration/`.

### Two-Factor Authentication

`/2fa/setup/`, `/2fa/manage/`, `/2fa/verify/` (v1.13) — **admin teachers only**.

- **Enrolment** — `two_factor_setup` generates a TOTP secret (`pyotp`) and renders it as a `qrcode` for any authenticator app, plus a set of one-time backup codes.
- **Login gate** — once `Teacher.two_factor_enabled` is set, `two_factor_verify` sits between password authentication and a usable session. Accepts a TOTP code or a backup code (which is consumed on use).
- **Manage** — disable 2FA, or rotate the backup codes.
- **Lockout recovery** — `manage.py reset_two_factor <email>` clears the secret and backup codes for an admin who lost their device.

### Parent Portal

`/portal/` (v1.9) — a separate, parent-facing surface with its own passwordless auth.

- **Magic-link login** — a parent enters their email; if it matches a `Parent`, `send_parent_magic_link_task` emails a signed link. The response is identical either way, so the endpoint never reveals whether an email is registered.
- **Token** — `ParentSessionToken` is single-use and time-limited; `consume` runs under `SELECT FOR UPDATE` so a link can't be redeemed twice concurrently.
- **Dashboard + payments** — the parent sees only their own children's payments. Receipts and the annual tax certificate download as PDFs.
- **Pay online (v1.11)** — when `STRIPE_SECRET_KEY` is set, a "Pagar online" button creates a Stripe Checkout session. The webhook (`stripe_webhook`) marks the payment completed. `STRIPE_WEBHOOK_SECRET` is **required in production** — with it unset the webhook skips signature verification, so any HTTP client could mark payments as paid.
- **Rate limited** — the login endpoint is behind the v1.10 IP rate limiter.

### PWA (Installable App)

(v1.12) — the app installs to a phone or desktop home screen.

- **`/manifest.webmanifest`** — name, theme colour (violet `#8b5cf6`), and icon set.
- **`/service-worker.js`** — **cache-first** for content-hashed static assets (they're immutable, so this is optimal) and network for everything else. `NoHtmlCacheMiddleware` marks dynamic HTML `no-cache` so navigation always revalidates and never pins stale asset hashes after a deploy.

---

## Testing

### Testing Overview

| Metric | Value |
|--------|-------|
| **Total tests** | 1,214 |
| **Test files** | 72 (46 unit + 26 integration) |
| **Coverage** | 95% (95.24% — 5,123 statements, 244 uncovered) |
| **Coverage thresholds** | **≥ 90%** (target, no warning) / **75-89%** (CI warning, pre-commit still blocks below 75) / **< 75%** (CI fails, pre-commit rejects the commit) |
| **Runtime** | ~50 seconds (parallel workers via `pytest-xdist -n auto`) |
| **Database** | PostgreSQL (same as production) — **always use `make test`** |
| **Framework** | pytest 9 + pytest-django + pytest-cov + pytest-xdist + pytest-randomly |
| **Type checking** | mypy + django-stubs (pre-commit hook) |
| **Security** | bandit security linter (pre-commit hook) |
| **Dependency audit** | pip-audit for CVE scanning |
| **Linting** | Ruff (check + format) via pre-commit hooks |
| **Settings** | `project/settings_test.py` |
| **Fixtures** | `conftest.py` — 18 shared fixtures |

```bash
make test                   # Inside Docker (PostgreSQL, parallel, with coverage)
make test unit              # Only unit tests (tests/unit/)
make test integration       # Only integration tests (tests/integration/)
make test coverage          # All tests + HTML coverage report
make test K=payment         # Filter by keyword
make test ARGS='-x --lf'    # Pass raw pytest flags through
make test-cov-gate          # Same as make test + fails if coverage < 75%
                            # (invoked by the pytest-coverage pre-commit hook)
```

> `make test` takes the suite selector as a **positional argument** (`make test unit`), not as a
> separate target. There are no `make test-unit` / `test-sqlite` / `test-coverage` / `test-fast`
> targets — `make test` and `make test-cov-gate` are the only two test targets in the Makefile.

**Coverage gates at every stage of the pipeline:**

| Stage | What enforces it | Behavior |
| --- | --- | --- |
| Pre-commit | `pytest-coverage` hook in `.pre-commit-config.yaml` → `make test-cov-gate` | Runs full suite inside Docker; rejects commit if coverage < 75%. Bypass with `git commit --no-verify` if containers are down (CI will still catch it). |
| CI (GitHub Actions) | `Check coverage threshold` step in `ci.yml` after `Run tests` | Parses `coverage.xml`. **< 75% fails the job** with an `::error::` annotation. **75-89% passes with a `::warning::`** annotation (visible in the PR checks UI). **≥ 90% silent pass.** |
| Local dev | `pyproject.toml` `[tool.coverage.report].fail_under = 75` | Applies to any tool that reads the coverage config (e.g. `coverage report` standalone). Same 75% floor as the other stages. |

Raise `fail_under` in `pyproject.toml` and the `FLOOR` / `TARGET` values in `ci.yml` as coverage grows.

Tests split cleanly into two directories, each with a 1:1 file-to-source-module mapping:

- **[project/tests/unit/](project/tests/unit/)** — direct-call tests. No HTTP stack, no URL resolver, no template rendering. Service-layer, pure-function, Celery-task, model, and helper tests live here. Tests that exercise view-object internals via `RequestFactory` also belong here.
- **[project/tests/integration/](project/tests/integration/)** — full HTTP-stack tests. Django's test client sends real requests through `SimpleAuthMiddleware` → URL resolver → view → template renderer and back. Uses the `authenticated_client` fixture.

Within each file, related tests are grouped into classes. Where a large file absorbed "extra" content or gap-filling edge cases, a `# ===` comment divider marks the section and a separate class name (e.g. `TestEmailServiceExtra`, `TestStudentCreateViewErrors`) keeps the cohesion visible at a glance. Shared fixtures live in [`project/conftest.py`](project/conftest.py); pytest discovers both subdirectories automatically.

### Unit Tests

**48 files, 680 tests.** Direct-call tests — no HTTP stack, no URL resolver, no template rendering.

| File | Count | Coverage |
| --- | --- | --- |
| [`unit/test_models.py`](project/tests/unit/test_models.py) | 48 | Every model across `students`, `billing`, `core` — properties (`full_name`, `age`, `is_overdue`, `remaining_amount`, `is_paid`), `__str__`, unique constraints, FK behavior, academic-year helpers (`current_academic_year`, `academic_year_start_date`, `academic_year_end_date`), SiteConfiguration singleton, HistoryLog cap + debounce |
| [`unit/test_services.py`](project/tests/unit/test_services.py) | 27 | `PricingService` (all fee + discount combos), `EnrollmentService` (all plans, language cheque, sibling, both, minimum-amount floor, adult enrollment, edge cases), `PaymentService` (monthly + quarterly amounts, June bonus, academic month/quarter validation, payment completion), service error paths |
| [`unit/test_schedule_utils.py`](project/tests/unit/test_schedule_utils.py) | 63 | `core.schedule_utils` — the single source of truth for how a group's timetable is rendered into the welcome email. Mon–Thu row bands, the Friday per-cell `FRIDAY_TIMES` map (four overlapping sessions), `is_valid_slot` grid validation, out-of-range rows returning a placeholder instead of raising, and `get_group_schedule_lines` (ordering, day grouping, empty group, column collapsing) |
| [`unit/test_student_view_internals.py`](project/tests/unit/test_student_view_internals.py) | 25 | `StudentUpdateView` view-object method branches (quarterly, part-time, no enrollment, exception-handling) via `RequestFactory` to sidestep missing template, plus unreferenced helper functions `handle_student_form`, `student_detail`, `update_student` called directly |
| [`unit/test_expenses.py`](project/tests/unit/test_expenses.py) | 33 | `Expense` model + `ExpenseService` (v1.5): per-frequency `clean()` validation, monthly totals aggregation, `materialize_recurring` (monthly, 1st-of-month) and `materialize_recurring_for_date` (weekly `recurring_weekdays` CSV + yearly), idempotency on `generated_from` + exact `expense_date`, and `recurring_day` accepting the whole 1–31 range (29–31 clamp to the month's last day) |
| [`unit/test_coverage_boost.py`](project/tests/unit/test_coverage_boost.py) | 25 | Targeted branch fill-in across the fix set: recurring-expense task, Google Sheets service internals, `SmsService._get_client`, rate-limit edge cases, Stripe view edge cases, expense-form bad input, waiting-list branches, parent-portal edge branches |
| [`unit/test_tasks.py`](project/tests/unit/test_tasks.py) | 24 | Celery tasks called synchronously with `email_service` mocked: `send_welcome_email_task` (parent + adult-student + missing + failure paths), `send_birthday_email_task`, `send_birthday_emails_task`, `send_payment_reminders`, `send_generic_email_task`, `send_enrollment_confirmation_task` (success + missing + attachments + failure) |
| [`unit/test_review_fixes.py`](project/tests/unit/test_review_fixes.py) | 23 | Regression locks for the review-loop fixes, one class per bug so a revert fails loudly: Stripe replay safety + checkout URLs, async magic link, `ParentSessionToken` atomicity (`SELECT FOR UPDATE`), rate-limit count methods, waiting-list assign guards, payment-reminder dedup, audit-log PII scrubbing |
| [`unit/test_email_service.py`](project/tests/unit/test_email_service.py) | 19 | `EmailService.send_email`: string + list recipients, CC/BCC, attachments, inline images (existing + missing path), `fail_silently` on and off, exception-raises-when-not-silent, `send_bulk_emails` mixed success/failure, `get_email_config`, Django 6 inline-image API |
| [`unit/test_two_factor_service.py`](project/tests/unit/test_two_factor_service.py) | 17 | TOTP two-factor service (v1.13): `begin_enrolment`, `confirm_enrolment`, `verify_totp`, `verify_backup_code`, `verify_code`, `disable`, `rotate_backup_codes`, issuer-name derivation |
| [`unit/test_email_functions.py`](project/tests/unit/test_email_functions.py) | 17 | All convenience wrappers (`send_birthday_email`, `send_welcome_email`, `send_payment_reminder`, `send_monthly_report`, `send_enrollment_confirmation_email`, `send_quarterly_receipt_email`, `send_fun_friday_email`, `send_vacation_closure_email`, `send_tax_certificate_email`, `send_all_tax_certificates`) plus tax-certificate PDF generation branches |
| [`unit/test_stripe_service.py`](project/tests/unit/test_stripe_service.py) | 16 | Stripe service (v1.11, `httpx` — no SDK dependency): `is_configured`, `create_checkout_session`, `verify_webhook_signature`, `apply_webhook_event`, singleton accessor |
| [`unit/test_email_bug_hunt_fixes.py`](project/tests/unit/test_email_bug_hunt_fixes.py) | 16 | Round-2 email regression suite: `payment_reminder_simple` + birthday templates, Fun Friday image guard, welcome email fired `on_commit`, CLI batch per-recipient loop, birthday to all parents, birthday-task timezone (`localdate`), monthly-report template defaults |
| [`unit/test_rate_limit.py`](project/tests/unit/test_rate_limit.py) | 19 | Cache-backed IP rate limiter (v1.10): window counting, limit enforcement, per-key isolation, disabled path, and `_client_ip` validation through `ipaddress`. Also pins the `TRUSTED_PROXY_COUNT` behaviour — the client IP is read N hops from the RIGHT of `X-Forwarded-For`, so a spoofed prefix cannot rotate the rate-limit bucket |
| [`unit/test_waiting_list.py`](project/tests/unit/test_waiting_list.py) | 14 | Waiting List & Group Capacity (v1.1) models + helpers: `Group.max_students` capacity properties, `Student.is_waiting`/`waiting_since`, group-capacity summary, capacity-freed notification |
| [`unit/test_google_sheets_service.py`](project/tests/unit/test_google_sheets_service.py) | 14 | Google Sheets export service (v1.2): configuration detection (inline creds vs file path), student + payment export shaping, result object, lazy `_get_service` construction |
| [`unit/test_coverage_boost_2.py`](project/tests/unit/test_coverage_boost_2.py) | 13 | Second branch-fill pass: waiting-list exception branches, student-create waiting mode, context-processor exception branches, audit-signal branches, PDF-service academy-info fallback, expense validation, Stripe cross-parent guard, rate-limit disabled path |
| [`unit/test_context_processors.py`](project/tests/unit/test_context_processors.py) | 13 | `today_notifications`: expected keys, todos due today vs other day, scheduled apps on Friday vs Monday, monthly apps excluded on day 15, history count, unauthenticated early-return |
| [`unit/test_constants.py`](project/tests/unit/test_constants.py) | 13 | Pure functions: `calculate_discount` (flat/percentage/invalid/edge), `get_monthly_fee_by_schedule`, `get_enrollment_fee` |
| [`unit/test_sms_service.py`](project/tests/unit/test_sms_service.py) | 12 | Twilio SMS service (v1.8): `is_configured` (all three env vars), `send`, `send_to_parent` (opt-in gate via `Parent.sms_opt_in`), singleton accessor |
| [`unit/test_beat_commands.py`](project/tests/unit/test_beat_commands.py) | 12 | The Beat-task management-command wrappers (v1.14.2): each command runs its task synchronously via `.apply()`, `--recipient`/`--month`/`--year`/`--date`/`--days` forwarding, `materialize_recurring_expenses` flag validation (`--daily` vs monthly), real backlog-cleanup run (old done task deleted, fresh survives) |
| [`unit/test_returning_student_discount.py`](project/tests/unit/test_returning_student_discount.py) | 11 | Returning-student enrollment discount (v1.13): `is_returning_student` detection, `compute_enrollment_fee` with the discount applied, `SiteConfiguration` default, and the `update_site_config` API accepting the new field |
| [`unit/test_log_safe.py`](project/tests/unit/test_log_safe.py) | 11 | `core.log_safe.safe_log` — the log-injection sanitizer (v1.14.4): CR/LF stripping, control characters, non-string coercion, length clamping |
| [`unit/test_analytics_service.py`](project/tests/unit/test_analytics_service.py) | 11 | Reports analytics service (v1.7): financial summary, collection rate, retention snapshot, group utilisation, and the composed dashboard report |
| [`unit/test_transactions.py`](project/tests/unit/test_transactions.py) | 10 | Query helpers: `get_active_students`, `get_payments_for_last_two_school_years`, `get_all_payments_unrestricted` — ordering, select_related, school-year filtering |
| [`unit/test_teacher_user_sync.py`](project/tests/unit/test_teacher_user_sync.py) | 10 | `Teacher.ensure_user()` (create + link + sync + password) and the `post_save` mirror signal (`admin` -> `is_staff`/`is_superuser`, email/name/username sync) |
| [`unit/test_celery_config.py`](project/tests/unit/test_celery_config.py) | 10 | Beat-schedule sanity checks (v1.4): every entry in `app.conf.beat_schedule` names a task that actually exists, queue routing is set, and task autodiscovery finds all four apps |
| [`unit/test_forms.py`](project/tests/unit/test_forms.py) | 9 | `EnrollmentForm` validation + `create_enrollment()` delegation to `EnrollmentService` (quarterly, monthly full/part, manual amount, sibling checkbox, adult, below-minimum rejection) |
| [`unit/test_seed_teachers_command.py`](project/tests/unit/test_seed_teachers_command.py) | 8 | `manage.py seed_teachers`: creation, idempotent update, password-persistence rule (no overwrite once a teacher has a usable password), gap-stop iteration, missing-field skip |
| [`unit/test_parent_session_token.py`](project/tests/unit/test_parent_session_token.py) | 8 | Parent-portal session token (v1.9): `issue` (hashing + expiry), validity window, and single-use `consume` under concurrent access |
| [`unit/test_new_email_tasks.py`](project/tests/unit/test_new_email_tasks.py) | 8 | The two review-pass email tasks: `send_parent_magic_link_task` (v1.9) and `send_payment_receipt_email_task` (v1.11) — success, missing-record, and send-failure paths |
| [`unit/test_fun_friday_scheduling.py`](project/tests/unit/test_fun_friday_scheduling.py) | 8 | `FunFridayScheduledSend.is_due` semantics + `send_due_fun_friday_emails_task` drain: due rows sent + marked `sent_at`, future rows skipped, idempotent re-run never re-sends, end-to-end send through the real email backend |
| [`unit/test_beat_tasks.py`](project/tests/unit/test_beat_tasks.py) | 8 | The v1.4 Beat tasks themselves: `generate_monthly_payments_task` (creates the month's pending fees, idempotent) and `send_monthly_report_task` (default + explicit recipient) |
| [`unit/test_student_forms.py`](project/tests/unit/test_student_forms.py) | 7 | `StudentForm` + `ParentForm` validation: future birth date rejected, DNI minimum length, required fields, both date formats |
| [`unit/test_pdf_service.py`](project/tests/unit/test_pdf_service.py) | 7 | reportlab PDF service (v1.3): payment receipt, quarterly summary, and tax certificate generation — byte output, academy-info population |
| [`unit/test_payment_helpers.py`](project/tests/unit/test_payment_helpers.py) | 7 | `parse_date_value` (6 formats including invalid) + `payment_detail` AJAX helper called directly via `RequestFactory` |
| [`unit/test_exports.py`](project/tests/unit/test_exports.py) | 7 | Excel workbook generation via `openpyxl`: Students, Enrollments, Payments sheets + combined workbook; empty-database edge case |
| [`unit/test_audit_log.py`](project/tests/unit/test_audit_log.py) | 6 | Immutable audit trail (v1.10): the `post_save`/`post_delete` signal receivers write an `AuditLog` row with the contextvar actor, and the model rejects mutation after creation |
| [`unit/test_qa_error_middleware.py`](project/tests/unit/test_qa_error_middleware.py) | 5 | `QAErrorEmailMiddleware.process_exception` via `RequestFactory`: pass-through, disabled config, no support email, send success, send failure swallowed |
| [`unit/test_final_coverage.py`](project/tests/unit/test_final_coverage.py) | 5 | The last uncovered branches: waiting-list assign with a null group in JSON, welcome-email `on_commit` happy path, Stripe checkout `httpx` error, receipt-email PDF-generation error |
| [`unit/test_bugfix_regressions.py`](project/tests/unit/test_bugfix_regressions.py) | 31 | Regression guards for the v1.15.0 fix pass, each pinning a defect verified broken against the running app: adult-student payments crashing search + CSV export, quarterly discounts (sibling, language cheque, June), completed payments with no `payment_date` vanishing from income, non-idempotent quick-complete rewriting financial history, payments attaching to a finished enrollment, unvalidated choice fields, `str(e)` leaking to the browser, cancelled payments inflating "esperado", query strings that used to 500, negative prices, singleton deletion, and the `enrollment_amount` fallback |
| [`unit/test_error_handlers.py`](project/tests/unit/test_error_handlers.py) | 5 | `handler400`/`handler403`/`handler404`/`handler405`/`handler500` render with correct status codes |
| [`unit/test_decorators.py`](project/tests/unit/test_decorators.py) | 5 | `@qa_access_required`: allow when `IS_TESTING_ENV` + the request is a logged-in admin Teacher, 404 when not testing env / authenticated non-teacher / anonymous |
| [`unit/test_sms_tasks.py`](project/tests/unit/test_sms_tasks.py) | 4 | `send_payment_reminder_sms_task` (v1.8): opt-in parent gets the SMS, opted-out is skipped, missing payment and send failure handled |
| [`unit/test_payment_scheduling.py`](project/tests/unit/test_payment_scheduling.py) | 4 | `PaymentService.schedule_academic_year_payments`: monthly enrollment -> 10 pending payments (Sep-Jun) due at month end, quarterly -> 3 (Oct/Jan/Apr), idempotent on re-run, inactive student skipped |
| [`unit/test_email_service_year.py`](project/tests/unit/test_email_service_year.py) | 3 | Regression: the `year` context value used to be hard-coded to 2025 — it now tracks the current year in every rendered email |
| [`unit/test_testing_tools_helpers.py`](project/tests/unit/test_testing_tools_helpers.py) | 2 | `_git_info` helper: success path + non-zero returncode branch with `subprocess.run` mocked |

### Integration Tests

**27 files, 525 tests.** Full HTTP stack through Django's test client.

| File | Count | Coverage |
| --- | --- | --- |
| [`integration/test_app_form_views.py`](project/tests/integration/test_app_form_views.py) | 99 | Every email form GET page, POST `action=preview` (JSON HTML), `test_send` with/without EMAIL_TEST_* env vars, main send-to-parents for every form (fun_friday, payment_reminder, vacation_closure, tax_certificate, monthly_report, birthday, receipts x 3, newsletter, enrollment/welcome), Fun Friday persist-for-Monday-14:30 + immediate drain when the slot passed (v1.14.2), invalid-date fallbacks, missing-field errors, no-parents-with-email edge cases, per-recipient exception swallowing, welcome_form redirect |
| [`integration/test_views.py`](project/tests/integration/test_views.py) | 73 | Cross-cutting top-level HTTP coverage: auth flow, dashboard, `all_info`, student/parent list + detail + create + search, payment list + create + detail + CRUD + stats + CSV + validation, todos + history API, management admin, email form pages (parametrized), enrollment API, error pages (parametrized), schedule, Fun Friday, support, and the `/health/` probe (shallow stays DB-free, deep reports connectivity + migrations, token gating for row counts, 503 on an unreachable DB, exception text never reaching the client) |
| [`integration/test_payment_views.py`](project/tests/integration/test_payment_views.py) | 38 | All HTTP payment endpoints: list (search, stats), create (+ invalid parent + unexpected exception), detail-view (+ 404), update (JSON + FormData + all error branches), delete (success + exception 500), deactivate (success + exception 400), quick-complete (success + invalid method + broken JSON), get-details (success + exception), search payments/parents (short query + hits), validate student-parent (all branches), export DB to Excel |
| [`integration/test_student_views.py`](project/tests/integration/test_student_views.py) | 23 | `StudentListView` (search, exclude inactive, context), `StudentDetailView` (parents visible, 404), `StudentCreateView` (form + adult mode + success + full POST + error paths including invalid parent, existing-parent mode, create_sibling flag, email-task swallow), `search_students` JSON endpoint (results + short-query empty) |
| [`integration/test_testing_tools.py`](project/tests/integration/test_testing_tools.py) | 21 | QA dashboard `/testing/` gated by `@qa_access_required` (via `override_settings`): dashboard renders + git failure handled, `api_seed_database` (success + reset + command error 500 + non-QA 404), `api_create_backlog_task` (all branches + screenshot attached to the email but never stored + send/swallow), `api_update_backlog_task` (success + invalid status + 404), `api_toggle_error_email` (on + off + bad JSON) |
| [`integration/test_teacher_auth_flow.py`](project/tests/integration/test_teacher_auth_flow.py) | 21 | Login dispatcher branches (dev env-var vs `auth.User`-backed Teacher login), OAuth user creation/Teacher-linking, `_finalize_session_login` setting both `_auth_user_id` and `is_authenticated`, `SimpleAuthMiddleware` whitelist behaviour for non-admin Teachers (allowed routes, 403 JSON for `/api/*`, dashboard redirect with flash for HTML), template gating (sidebar swap, read-only management) |
| [`integration/test_management_views.py`](project/tests/integration/test_management_views.py) | 19 | `gestion_view` + `update_site_config` (all fields + bad JSON), `create_teacher` (success + duplicate + missing field + bad JSON), `create_group` (success + missing fields + duplicate + nonexistent teacher + bad JSON), `api_get_teachers`, `update_enrollment_modality` (success + invalid + no enrollment + student not found), `language_cheque_students` |
| [`integration/test_two_factor_views.py`](project/tests/integration/test_two_factor_views.py) | 17 | 2FA views and the login gate (v1.13): setup page (QR + secret), manage page (disable, rotate backup codes), the login gate flow (TOTP accepted, backup code accepted, wrong code rejected), and the `reset_two_factor` management command |
| [`integration/test_waiting_list_views.py`](project/tests/integration/test_waiting_list_views.py) | 16 | Waiting List & Group Capacity views (v1.1): waiting-list page, `assign_from_waiting_list` (capacity checks + payment scheduling), `add_to_waiting_list`, student list excludes waiting students, dashboard waiting widget |
| [`integration/test_dashboard_views.py`](project/tests/integration/test_dashboard_views.py) | 15 | `home` view quote-cookie branches (valid cookie, corrupt cookie -> API, API failure, API empty, `[AUTH]` placeholder filtered, with pending payments), `all_info` sort variants (default, first_name, last_name, id_asc, payments_sort=student_asc) |
| [`integration/test_parent_portal.py`](project/tests/integration/test_parent_portal.py) | 14 | Parent portal (v1.9): magic-link request + token issue, link consumption establishing a scoped session, and the portal pages (payment list, receipt download) restricted to that parent's own children |
| [`integration/test_schedule_views.py`](project/tests/integration/test_schedule_views.py) | 13 | Schedule page (groups + slots in context), `save_schedule_slot` (assign + clear + reject GET + invalid JSON), Fun Friday page (loads, excludes adults, with attendance) |
| [`integration/test_auth_oauth.py`](project/tests/integration/test_auth_oauth.py) | 13 | OAuth callback flow with `google_auth_oauthlib.flow.Flow` mocked: state missing, state mismatch, `fetch_token` failure, id-token verification failure, email whitelist mismatch, successful session establishment; login view extras (already-auth redirect, missing env, OAuth-available flag); logout clears session |
| [`integration/test_expense_views.py`](project/tests/integration/test_expense_views.py) | 11 | Expense CRUD endpoints (v1.5): list page with monthly totals, create (valid + per-frequency validation errors), delete |
| [`integration/test_password_reset.py`](project/tests/integration/test_password_reset.py) | 10 | Full password-reset round-trip: request form renders, valid email triggers branded HTML email send, confirm page accepts new password with valid uidb64+token, complete page renders, all four URLs reachable while unauthenticated (`SimpleAuthMiddleware.PUBLIC_PREFIXES` exemption) |
| [`integration/test_middleware.py`](project/tests/integration/test_middleware.py) | 10 | `SimpleAuthMiddleware`: public paths (login, health, static, media, OAuth prefix), protected paths redirect to login, authenticated requests pass; `NoHtmlCacheMiddleware` marks dynamic HTML `no-cache` while leaving hashed static assets immutable |
| [`integration/test_todo_views.py`](project/tests/integration/test_todo_views.py) | 8 | `create_todo` (missing text + missing date + invalid date + success), `complete_todo`, `history_list` (default + offset + invalid offset) |
| [`integration/test_stripe_views.py`](project/tests/integration/test_stripe_views.py) | 8 | Stripe endpoints (v1.11): `create_checkout_link` (configured + not configured + cross-parent guard) and the webhook (valid signature applies the payment, bad signature rejected, unknown event ignored) |
| [`integration/test_pwa_views.py`](project/tests/integration/test_pwa_views.py) | 8 | PWA endpoints (v1.12): `web_manifest` (JSON shape, icons) and `service_worker` (correct content type, cache-first strategy for hashed static) |
| [`integration/test_parent_views.py`](project/tests/integration/test_parent_views.py) | 8 | `ParentCreateView`: GET renders, POST new + existing DNI + invalid + exception-triggers-form-invalid |
| [`integration/test_auth_views.py`](project/tests/integration/test_auth_views.py) | 7 | Login view: render for unauth'd, redirect for authenticated, valid + invalid credentials, logout, OAuth redirect (no creds -> login) |
| [`integration/test_sheets_views.py`](project/tests/integration/test_sheets_views.py) | 6 | Google Sheets export view (v1.2): `/api/sheets/export/` success, 503 when unconfigured, and error propagation |
| [`integration/test_fun_friday_attendance_views.py`](project/tests/integration/test_fun_friday_attendance_views.py) | 6 | `toggle_fun_friday_this_week` (adult rejected + toggle on/off), `add_fun_friday_attendance` (success + invalid), `remove_fun_friday_attendance` (success + invalid) |
| [`integration/test_support_views.py`](project/tests/integration/test_support_views.py) | 5 | `submit_support_ticket`: success (send_mail called), short message rejected, no support email configured -> 500, bad JSON, unexpected exception |
| [`integration/test_reports_view.py`](project/tests/integration/test_reports_view.py) | 4 | Reports dashboard (v1.7): the HTML page renders every analytics block, and the PDF export returns a reportlab document |
| [`integration/test_receipt_view.py`](project/tests/integration/test_receipt_view.py) | 2 | Payment-receipt PDF endpoint (v1.3): authorised download returns a PDF, unauthorised is rejected |
| [`integration/test_bugfix_security_and_features.py`](project/tests/integration/test_bugfix_security_and_features.py) | 57 | Security regressions + the v1.15.0 feature additions. Security: the three stored-XSS sinks (history feed, student autocomplete, schedule JSON block), rate-limit bypass via a spoofed `X-Forwarded-For`, parent-portal session fixation, the service worker caching `/login/`, schedule-slot validation, and teachers created in the UI being able to activate their account. Features: the short waiting-list form, the waiting-list round trip, the payment-history PDF, month and group filters, backlog export, Fun Friday double-send, newsletter fallback, adult receipts, payment receipt emails, enrollment churn, and Spanish enrollment labels |

### Coverage Report

Live snapshot from the last full run (`make test`) — the 29 source files below 100% coverage:

| File | Stmts | Miss | Cover | Missing lines |
| --- | --- | --- | --- | --- |
| `billing/models.py` | 225 | 22 | 90% | 319-325, 416, 421, 559-587, 600, 602, 614 |
| `billing/services/enrollment_service.py` | 86 | 6 | 93% | 137, 147-150, 163, 180 |
| `billing/services/payment_service.py` | 98 | 2 | 98% | 42, 49 |
| `billing/services/pdf_service.py` | 148 | 2 | 99% | 314-315 |
| `billing/services/stripe_service.py` | 102 | 3 | 97% | 139-140, 168 |
| `comms/services/email_service.py` | 63 | 1 | 98% | 59 |
| `comms/services/sms_service.py` | 50 | 3 | 94% | 58, 63-64 |
| `comms/tasks.py` | 239 | 4 | 98% | 386, 477, 626, 706 |
| `core/audit_models.py` | 25 | 1 | 96% | 66 |
| `core/audit_signals.py` | 92 | 6 | 93% | 109, 115, 131, 142-143, 148 |
| `core/context_processors.py` | 31 | 1 | 97% | 22 |
| `core/middleware.py` | 81 | 5 | 94% | 52-57, 172, 217-218 |
| `core/models.py` | 123 | 5 | 96% | 48, 100, 203, 211, 236 |
| `core/services/google_sheets_service.py` | 99 | 9 | 91% | 74-76, 112-118 |
| `core/tasks.py` | 20 | 5 | 75% | 41-46 |
| `core/transactions.py` | 19 | 1 | 95% | 28 |
| `core/views/app_forms.py` | 611 | 44 | 93% | 64, 150-152, 166-169, 186-187, 280-281, 304-305, 355-357, 388, 543-545, 551, 687, 709, 728, 802, 806, 828, 839, 915, 941, 954, 967, 979, 1002-1005, 1120, 1166, 1194-1198, 1226-1229 |
| `core/views/auth.py` | 167 | 18 | 89% | 45-48, 179, 183-199, 250, 285, 346-355 |
| `core/views/dashboard.py` | 131 | 6 | 95% | 129-136, 177, 193 |
| `core/views/expenses.py` | 83 | 6 | 93% | 52, 55-56, 84, 105-106 |
| `core/views/parent_portal.py` | 101 | 3 | 97% | 157, 184, 204 |
| `core/views/payments.py` | 322 | 16 | 95% | 57-58, 81, 252-257, 307, 313-314, 453, 500-501, 523-524, 538-539 |
| `core/views/students.py` | 345 | 20 | 94% | 59-60, 109, 173, 219-220, 362, 385, 393, 411-417, 422-424, 620, 623-625 |
| `core/views/testing_tools.py` | 184 | 28 | 85% | 145, 147, 203-205, 298-302, 315, 333-334, 361-409 |
| `core/views/two_factor.py` | 90 | 9 | 90% | 38, 49-50, 170-172, 177-178, 193 |
| `core/views/waiting_list.py` | 133 | 9 | 93% | 105, 129-133, 159, 198, 339 |
| `students/forms.py` | 67 | 2 | 97% | 182-183 |
| `students/models.py` | 213 | 3 | 99% | 350, 367-368 |
| `students/parent_portal_models.py` | 41 | 1 | 98% | 44 |

**56 files** have 100% coverage (skipped above). Total coverage: **95%** (95.24%) across 5,123 statements, 244 uncovered. Coverage is **very good**. Coverage is enforced at three levels: pre-commit hook (>= 75%), CI hard floor (>= 75%), and CI warning (< 90%).

> **Reading the number consistently (fixed in v1.14.7).** The CI test step runs with
> `working-directory: project`, but `[tool.coverage.run]` — including the `omit` list for
> migrations, management commands and `admin.py` — lives in the repo-root `pyproject.toml`.
> Coverage only looks for config in the *current* directory, so CI was silently ignoring the
> omit list: 42 extra files, ~993 extra statements, and a reported **86.44%** instead of
> **95.49%**. That fired the `< 90%` warning on every run and sent the wrong figure to Codecov.
> `ci.yml` now passes `--cov-config=../pyproject.toml`, so CI, `make test` and
> `make test-cov-gate` all report the same number.

---

## Migrations

All migrations were regenerated from scratch during the v1.0.0 multi-app split.

| App | Migration | Changes | Depends On |
|-----|-----------|---------|------------|
| `students` | `0001_initial` | Teacher, Group, Parent, Student, StudentParent | — |
| `students` | `0002` | Student gender field, StudentParent UniqueConstraint | `students.0001` |
| `students` | `0003_teacher_user` | Adds `Teacher.user` OneToOneField → `auth.User` (nullable, `on_delete=SET_NULL`) | `students.0002`, `auth` |
| `students` | `0004_waiting_list_and_group_capacity` | `Student.is_waiting`, `waiting_since`, `Group.max_students` (v1.1) | `students.0003` |
| `students` | `0005_add_parent_sms_opt_in` | `Parent.sms_opt_in` (v1.8) | `students.0004` |
| `students` | `0006_add_parent_session_token` | `ParentSessionToken` — parent-portal magic-link auth (v1.9) | `students.0005` |
| `students` | `0007_add_teacher_two_factor` | `Teacher.two_factor_secret / _enabled / _backup_codes` (v1.13) | `students.0006` |
| `billing` | `0001_initial` | SiteConfiguration, EnrollmentType, Enrollment, Payment | `students.0001` |
| `billing` | `0002` | Enrollment academic_year index | `billing.0001`, `students.0002` |
| `billing` | `0003_add_expense_model` | `Expense` — recurring templates + auto-materialised rows (v1.5) | `billing.0002` |
| `billing` | `0004_add_payment_stripe_fields` | `Payment.stripe_session_id`, `stripe_payment_intent` (v1.11) | `billing.0003` |
| `billing` | `0005_add_returning_student_discount` | `SiteConfiguration.returning_student_enrollment_discount` (v1.13) | `billing.0004` |
| `billing` | `0006_expense_recurring_frequency_and_more` | `Expense.recurring_frequency` / `recurring_month` / `recurring_weekdays`, `recurring_day` widened — the three recurring cadences (monthly / weekly / yearly) | `billing.0005` |
| `core` | `0001_initial` | TodoItem, HistoryLog, FunFridayAttendance, ScheduleSlot | `students.0001` |
| `core` | `0002` | UniqueConstraint for FunFridayAttendance and ScheduleSlot | `core.0001`, `students.0002` |
| `core` | `0003_qa_backlog_and_config` | QA backlog model and config fields | `core.0002` |
| `core` | `0004_add_audit_log` | `AuditLog` model + expanded HistoryLog action choices (v1.10) | `core.0003` |
| `core` | `0005_funfridayscheduledsend` | `FunFridayScheduledSend` — persisted scheduled Fun Friday announcements (v1.14.2) | `core.0004` |
| `comms` | — | (no models) | — |

```bash
# After modifying models:
make makemigrations   # Creates migrations for all 4 apps
make migrate          # Applies them
```

---

## Security

This section documents every security decision, mechanism, and configuration in the project.

### Authentication

**Mechanism**: Django `ModelBackend` everywhere, with the login view dispatching by `DJANGO_ENV` and `SimpleAuthMiddleware` enforcing role-based gating on top.

| Component | File | How it works |
|-----------|------|-------------|
| Login view (dev) | `core/views/auth.py` | When `DJANGO_ENV=development`, compares username/password against `LOGIN_USERNAME`/`LOGIN_PASSWORD` env vars and get-or-creates a matching Django superuser so `/admin/` works. No hardcoded fallbacks — if env vars are missing, login is refused. |
| Login view (testing/prod) | `core/views/auth.py` | Authenticates Teachers against `auth.User` via `django.contrib.auth.authenticate` — email is the username, password is hashed by Django's PBKDF2. Teachers are linked to a User via `Teacher.user` (OneToOne) and seeded from `TEACHER_SEED_<N>_*` env vars by the `seed_teachers` command. |
| Google OAuth | `core/views/auth.py` | Full OAuth 2.0 code flow via `google-auth-oauthlib`. State token stored in session and verified on callback. ID token verified server-side via Google's public keys. Only the email matching `GOOGLE_ALLOWED_EMAIL` (or `EMAIL_HOST_USER` / `DJANGO_SUPERUSER_EMAIL`) is authorized. Get-or-creates a Django superuser and links it to an existing Teacher by email — the same session also grants `/admin/`. |
| Session finalisation | `core/views/auth.py::_finalize_session_login` | Every successful login (dev, Teacher, OAuth) calls `django.contrib.auth.login(...)` to set `_auth_user_id` *and* sets the legacy `session["is_authenticated"]` flag used by the middleware. |
| Auth middleware — Layer 1 | `core/middleware.py` | `SimpleAuthMiddleware` protects all routes. Public URLs use exact match for `/login/` and prefix match for `/health/`, `/static/`, `/media/`, `/auth/google/`, `/password-reset/`. All other paths require `session["is_authenticated"]`. |
| Auth middleware — Layer 2 | `core/middleware.py::NON_ADMIN_ALLOWED_URL_NAMES` | When the session belongs to a Teacher with `admin=False`, requests are restricted to a URL-name whitelist. Admin-only routes return 403 JSON on `/api/*` or redirect to the dashboard with a flash message on HTML routes. Admin Teachers and OAuth/dev-superuser sessions bypass this layer. |
| Password reset | `core/views/password_reset.py` | Branded subclasses of Django's built-in views, served at `/password-reset/...`. URLs are in `PUBLIC_PREFIXES` so a locked-out teacher can still reach them. Uses Django's signed token machinery; HTML email rendered from `emails/password_reset.html`. |
| OAuth credentials | `core/views/auth.py` | Google tokens (access, refresh) are stored in session server-side. `client_secret` is never sent to the frontend. Allowed email check is backend-only. |
| Two-factor (TOTP) | `core/views/two_factor.py` + `core/services/two_factor_service.py` | v1.13. Admin Teachers can enrol via `/two-factor/setup/` — the setup page renders a QR (pyotp provisioning URI) + 8 one-time backup codes (shown once, sha256-hashed at rest). After enrolment, the login flow stashes the user id on the session (`_2fa_pending_user_id`) WITHOUT setting `is_authenticated` and redirects to `/two-factor/verify/`. Only after a valid TOTP or backup code does `_finalize_session_login` promote the session. Rate-limited to 6/min/IP. Recovery: `manage.py reset_two_factor <email>` from the server console. Google OAuth also takes the 2FA gate — the OAuth-confirmed email is only one factor. |
| Audit log | `core/audit_models.py` + `core/audit_signals.py` | v1.10. Immutable `AuditLog` model records every create / update / delete on tracked models (Student, Parent, Teacher, Group, Enrollment, Payment, SiteConfiguration, Expense) with actor + per-field diff. `AuditActorMiddleware` stashes the current user in a `contextvars.ContextVar` (WSGI-local + ASGI-safe). Per-model field allow-list keeps GDPR-sensitive PII (Parent.dni/iban/email/phone, Teacher.email, password hashes) out of the JSON payload. |
| Rate limiting | `core/rate_limit.py` | v1.10. Cache-backed IP throttle (`cache.add` + `cache.incr` — atomic on Redis and memcached, closes the TOCTOU race a plain `get→set` would open). Applied to `/login/` (5/min/IP), `/parent/login/` (5/min/IP), `/parent/login/<token>/` (20/min/IP against brute force), and `/two-factor/verify/` (6/min/IP). `RATELIMIT_ENABLE=False` bypasses in tests. |

**Design decisions**:

- **Django User model is now in use** — testing and production both authenticate Teachers through `auth.User` (hashed passwords + Django's auth machinery). Dev still uses env-var basic-auth for ergonomic reasons; an underlying superuser is auto-mirrored so the experience matches.
- **Two-tier role model** — admin Teachers see everything; non-admin Teachers see the dashboard, students, fun friday, and a read-only management page. Role mapping flows from `Teacher.admin` → `auth.User.is_staff`/`is_superuser` via `Teacher.ensure_user` and a `post_save` signal.
- **2FA is opt-in per admin** — enabling it takes 30 seconds (scan a QR, type one code, save 8 backup codes). Recommended for every production admin. Non-admin Teachers can't enrol (they don't have sensitive endpoints); dev-mode env-var basic-auth doesn't have a Teacher record so it's never prompted for a second factor.
- Google OAuth is optional — if `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are not set, the OAuth button is hidden.
- `OAUTHLIB_INSECURE_TRANSPORT` is only set when `DEBUG=True` (for local HTTP testing).
- The password-reset email is the only path to activate a Teacher whose seed block omits `..._PASSWORD` — Gmail SMTP must work in any environment that issues real reset links.

### Session & Cookie Configuration

All cookie flags are enforced via `settings.py` with environment-aware defaults:

| Setting | Development | Production | Purpose |
|---------|------------|------------|---------|
| `SESSION_COOKIE_AGE` | 21600 (6h) | 21600 (6h) | Session lifetime. `SESSION_SAVE_EVERY_REQUEST=True` refreshes it on every request, making this an **inactivity** timeout |
| `SESSION_COOKIE_HTTPONLY` | `True` | `True` | Prevents JavaScript access to session cookie |
| `SESSION_COOKIE_SAMESITE` | `Lax` | `Lax` | **Not `Strict`.** The Google OAuth callback is a cross-site top-level navigation, and `Strict` withholds the session cookie on that hop — `google_oauth_state` goes missing and every OAuth login fails with "Estado OAuth inválido". `Lax` still blocks cross-site POSTs and subresource requests |
| `SESSION_COOKIE_SECURE` | `False` | `True` | Requires HTTPS for cookie transmission |
| `CSRF_COOKIE_HTTPONLY` | `False` | `True` | Prevents JavaScript access to CSRF cookie in production |
| `CSRF_COOKIE_SAMESITE` | `Lax` | `Strict` | Prevents cross-site CSRF cookie leakage |
| `CSRF_COOKIE_SECURE` | `False` | `True` | Requires HTTPS for CSRF cookie |

Production defaults are applied automatically when `DEBUG=False` — no manual override needed in env vars.

### CSRF Protection

- Django's `CsrfViewMiddleware` is active in the middleware stack.
- All POST endpoints receive CSRF validation. JavaScript AJAX requests use `getCsrfToken()` (reads from cookies) and send via `X-CSRFToken` header.
- `CSRF_TRUSTED_ORIGINS` is configured per deployment via the `CSRF_TRUSTED_ORIGINS` env var (see [DEPLOYMENT.md](DEPLOYMENT.md)).
- Only exception: `@csrf_exempt` on `/health/` endpoint (GET-only, returns `{"status": "healthy"}`).

### Transport Security (HTTPS)

When `DEBUG=False`, the following are enforced via `settings.py`:

| Setting | Value | Effect |
|---------|-------|--------|
| `SECURE_SSL_REDIRECT` | `True` | All HTTP requests redirected to HTTPS |
| `SECURE_HSTS_SECONDS` | `31536000` (1 year) | Browser remembers to use HTTPS |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `True` | HSTS applies to all subdomains |
| `SECURE_HSTS_PRELOAD` | `True` | Eligible for browser HSTS preload lists |

All settings are environment-controlled and only activate when `DEBUG=False`.

### Security Headers

| Header | Setting | Value | Effect |
|--------|---------|-------|--------|
| `X-Frame-Options` | `X_FRAME_OPTIONS` | `DENY` | Prevents clickjacking — page cannot be embedded in iframes |
| `X-Content-Type-Options` | `SECURE_CONTENT_TYPE_NOSNIFF` | `True` | Prevents MIME type sniffing attacks |
| `X-XSS-Protection` | `SECURE_BROWSER_XSS_FILTER` | `True` | Enables browser XSS filter (legacy, supplementary) |

### Infrastructure & Deployment

#### Docker

| Decision | Implementation |
|----------|---------------|
| Non-root container | `Dockerfile` creates user `django` (uid 1000) and runs as `USER django` |
| Multi-stage build | Builder stage compiles dependencies; runtime stage uses `python:3.12-slim` without build tools |
| No secrets in image | `.dockerignore` excludes `.env*`, `scripts/`, `.git/` |
| DB port restricted | `docker-compose.yml` binds PostgreSQL to `127.0.0.1:5432` only (not exposed to network) |
| Health checks | Database has auth-checking healthcheck; web service uses `/health/` endpoint |
| Seed script guard | `scripts/reset_seed_dev_data.py` aborts if `DJANGO_ENV=production` or `DEBUG=False` |

#### Google Cloud Run

Full deployment walkthrough in [DEPLOYMENT.md](DEPLOYMENT.md). Security-relevant decisions:

| Decision | Implementation |
|----------|---------------|
| Secret Manager | All credentials (`DJANGO_SECRET_KEY`, `LOGIN_*`, `EMAIL_SECRET`, `POSTGRES_*`, `GOOGLE_*`) injected at startup from GCP Secret Manager |
| Cloud SQL Auth Proxy | PostgreSQL connection goes through the IAM-authenticated proxy socket mounted by Cloud Run (`/cloudsql/...`). The instance keeps a public IP but has **zero authorized networks**, so the proxy is the only reachable path — no VPC connector needed |
| Autoscaling | min=0 (cold starts acceptable) or min=1 (~$7/mo) for always-warm, max=2 instances |
| Probes | Startup probe + liveness probe on `/health/` |
| TLS | Managed automatically by Cloud Run (custom domain + Google-managed certificate) |
| SSL enforced | `SECURE_SSL_REDIRECT=True`, all cookie secure flags enabled when `DEBUG=False` |
| SameSite cookies | `SESSION_COOKIE_SAMESITE=Lax` (required for the OAuth callback), `CSRF_COOKIE_SAMESITE=Strict`, `CSRF_COOKIE_HTTPONLY=True` |

#### Cold-start behaviour on Cloud Run

`entrypoint.sh` runs `migrate` unconditionally on container start, and
`collectstatic --noinput --clear` whenever `DJANGO_ENV` is `testing` or `production`.
In Docker (dev) and on the testing VM that happens once per `docker compose up`.
**On Cloud Run with `min-instances=0` it happens on every cold start**, because each new
container executes the entrypoint from scratch.

| Effect | Detail |
|--------|--------|
| Slow first request | The morning's first hit waits for container pull + `migrate` + a full `collectstatic --clear` before Gunicorn binds — roughly 20-40 s, versus 5-10 s for a bare cold start |
| Redundant DB round trip | `migrate` opens a Cloud SQL connection and inspects `django_migrations` on every boot, even when nothing is pending |
| Concurrent-migration risk | With `max-instances` > 1, two containers can cold-start at the same moment and run `migrate` concurrently. Django holds no cross-process migration lock; PostgreSQL DDL locks usually serialise it safely, but a failed/partial apply is possible. Low probability, not zero |
| Wasted static rebuild | `--clear` wipes and regenerates `/app/staticfiles` (hashed + compressed by `CompressedManifestStaticFilesStorage`) on every boot, producing byte-identical output each time |

**This is accepted for the initial production rollout.** Four known users, effectively one
cold start per morning, and no schema churn between deploys. It is recorded here so it stays
a deliberate trade-off rather than a surprise during an incident.

**Mitigations available today, no code change:**

- `--min-instances=1` — removes cold starts entirely, ~$7/month (see [DEPLOYMENT.md](DEPLOYMENT.md#slow-cold-starts))
- Startup CPU boost — free, shortens the startup window
- `--max-instances=1` — eliminates the concurrent-migration window at the cost of throughput

**Planned fix** (tracked, not yet implemented):

1. Move `collectstatic` into the Dockerfile build stage so `staticfiles/` ships baked into the
   image, and drop it from `entrypoint.sh`. Note the build then needs a dummy `DJANGO_SECRET_KEY`
   at `RUN` time, since `settings.py` refuses to import with the dev default when `DEBUG=False`.
2. Gate `migrate` behind a `RUN_MIGRATIONS_ON_START` env var — default `false` on Cloud Run, where
   the dedicated `fiveaday-migrate` Cloud Run Job is the single, serialised place migrations apply.
3. Leave both steps enabled in development and testing, where running them on every `up` is the
   convenient behaviour.

Together these cut the production cold start to container pull + Gunicorn boot, and make schema
changes an explicit, one-at-a-time operation.

### Secrets Management

| Rule | Implementation |
|------|---------------|
| No hardcoded credentials | Dev auth refuses login when `LOGIN_USERNAME`/`LOGIN_PASSWORD` are missing; testing/prod auth refuses any password not matching a hashed `auth.User` record |
| No secrets in YAML | Production credentials live in GCP Secret Manager, injected into Cloud Run at startup — never in the repo |
| No secrets in GitHub Actions for deploy | CI uses only non-production Gmail SMTP + Codecov upload token. Production deploy runs manually with the operator's `gcloud` credentials |
| No secrets in Docker image | `.dockerignore` excludes all `.env*` files |
| `.gitignore` coverage | `.env*` pattern excludes all env file variants |
| Production startup validation | `settings.py` raises `ValueError` if `SECRET_KEY` is the dev default and `DEBUG=False` |

### Email Security

| Decision | Implementation |
|----------|---------------|
| TLS enforced | `EMAIL_USE_TLS=True`, port 587 (STARTTLS) |
| App Password | Uses Gmail App Password (not account password) via `EMAIL_SECRET` env var |
| `fail_silently` | Defaults to `False` for single sends (raises on failure); `True` for bulk sends (logs failures) |
| No PII in logs | Celery tasks log by ID (`student_id=X`) not by name/email/DNI |
| Template auto-escaping | All email templates use Django's default auto-escaping — `{{ variable }}` is HTML-safe |
| Inline images | Attached via MIME `Content-ID` headers, not external URLs |

### Data Protection & Input Validation

| Layer | Mechanism |
|-------|-----------|
| **Models** | `DecimalField` with `MinValueValidator` for all money fields. `UniqueConstraint` for enrollment/schedule/attendance integrity. `PROTECT` on foreign keys prevents orphaned records. |
| **Forms** | Django `ModelForm` with `clean_*()` validators. Date fields accept `%Y-%m-%d` and `%d/%m/%Y`. DNI validated for minimum length. |
| **Views** | `get_object_or_404` for safe lookups. `@require_http_methods` on all AJAX endpoints. `Decimal(str(...))` for safe numeric conversion. `json.JSONDecodeError` caught explicitly. |
| **Services** | `transaction.atomic()` wraps multi-model writes (enrollment creation, payment completion). `ValueError` raised for missing config. |
| **GDPR** | `gdpr_signed` field on Student. No student data exposed without authentication. PII removed from log messages. |

### Logging & Monitoring

- Console logging via `StreamHandler` with configurable `LOG_LEVEL` env var.
- Separate loggers for `django` framework and project modules.
- `HistoryLog` model tracks user actions (payment completed, student enrolled, config updated) — capped at 1000 entries with automatic cleanup.
- Celery tasks log by entity ID, not PII.

### Future Security Improvements

These are not blockers but would strengthen the system for scale or compliance:

| Priority | Improvement | Why |
|----------|------------|-----|
| **High** | Content-Security-Policy header | Prevents XSS. Currently absent — Tailwind CDN requires `unsafe-inline` for styles, but scripts can be locked down. |
| **High** | Referrer-Policy header (`strict-origin-when-cross-origin`) | Prevents referrer leakage to external links. Currently absent. |
| **Medium** | Enforce 2FA for all admins (not opt-in) | Currently opt-in per admin. A `Teacher.admin=True` save could refuse until 2FA is enrolled. |
| **Medium** | Session rotation on OAuth login (`request.session.create()`) | Prevents session fixation. Currently session ID persists through OAuth flow. |
| **Medium** | Inactivity timeout (30 min idle logout) | 24h session is long for sensitive student data. |
| **Medium** | Permissions-Policy header | Disables camera, microphone, geolocation APIs the app doesn't need. |
| **Medium** | `Argon2` password hasher | Stronger than the default PBKDF2 now used for Teacher passwords. Switch `PASSWORD_HASHERS` once `argon2-cffi` is added to deps. |
| **Low** | Request ID tracking (`X-Request-ID` middleware) | Enables log correlation across services. |
| **Low** | `detect-secrets` pre-commit hook | Prevents accidental secret commits in the future. |
| **Low** | Web Application Firewall (WAF) rules at cloud provider level | Blocks common attack patterns before they reach Django. |

**Shipped since the last README revision** (all now built-in, moved out of this list):

- ✅ Rate limiting on login + parent-portal login + 2FA verify (v1.10 + v1.13)
- ✅ Security event audit log — every admin CRUD action recorded in `AuditLog` (v1.10)
- ✅ Two-factor authentication (TOTP + backup codes) for admin Teachers (v1.13)

---

## Testing Environment (QA)

> **This section is for testers, teachers, and anyone helping us try out the application before it goes live.**
> You do not need to be a programmer to use the testing environment. If something looks wrong or confusing, that is exactly the kind of feedback we need.

### What is the testing environment?

The testing environment is a copy of the real application that runs on the internet, just like the final version will. It looks and works exactly the same, but it uses **fake data** — fake students, fake parents, fake payments. Nothing you do here affects real people or real money.

Think of it as a **rehearsal stage**: you can click anything, try any feature, and even break things. We can always reset it.

### How to access it

| | |
|---|---|
| **Web address** | [http://34.26.130.187:8000/](http://34.26.130.187:8000/) — the `testing` branch, auto-deployed to the GCP `e2-micro` VM |
| **Username** | Your Teacher email — seeded into the system via `TEACHER_SEED_<N>_EMAIL` |
| **Password** | The initial password set by the development team (or set yours via the "¿Has olvidado tu contraseña?" link if you weren't given one) |

Credentials are seeded from `TEACHER_SEED_<N>_*` env vars in `.env.testing` and are **never committed to the repository**. Ask the development team if you need them. If you weren't issued a password, use the password-reset link on the login page — you'll receive an email with a one-time activation link.

1. Open the web address in your browser (Chrome, Firefox, Safari, or Edge all work).
2. You will see a login page. Type the username and password you were given.
3. After logging in you will see the **Dashboard** — the home screen with today's tasks, pending payments, and birthdays.

### What you can test

Here is a quick checklist of things to try. If anything does not work, take note of what happened and tell the development team.

- **Dashboard** — Does it load? Do the numbers make sense?
- **Students** — Can you see the list of students? Open a student's profile? Search by name?
- **Create a student** — Fill in the form and save. Does the new student appear in the list?
- **Payments** — Open the payments page. Try marking a payment as completed. Try filtering by status.
- **Schedule** — Open the weekly schedule. Can you see groups assigned to time slots?
- **Fun Friday** — Toggle a student's attendance on or off.
- **Email forms** (Apps section) — Open each email form. You do not need to send real emails; just verify the forms load correctly.
- **Management** — Can you update the site configuration (pricing)? Create a teacher or group?
- **General navigation** — Does the sidebar work? Do all links go to the right page? Is the text readable?
- **Testing Tools** (the blue "info" icon at the bottom of the sidebar) — This is your QA control panel:
  - **Project Info** — shows the current software version, last commit, server status
  - **Error Reporting toggle** — turn this ON so every server error is automatically emailed to the development team with full details
  - **Database Seeding** — click to populate the database with test data, or wipe and start fresh
  - **QA Backlog** — report bugs and suggestions directly from this page; each new task is emailed to the development team

### How to report a problem

When something goes wrong, please note:

1. **What page you were on** — copy the web address from your browser's address bar, or describe the page ("I was on the payments list").
2. **What you did** — "I clicked the green Complete button on a payment" or "I searched for a student named Sofia".
3. **What happened** — "The page showed an error" or "Nothing happened" or "It showed the wrong information".
4. **Screenshot** — If possible, take a screenshot (press the Print Screen key or use the Snipping Tool on Windows).

Send this information to the development team. Even a short message like "The payments page shows an error when I click Export" is helpful.

### Error pages you might see

| Page | What it means |
|------|--------------|
| **Login page** (you are sent back to login) | Your session expired. Just log in again. |
| **Page not found (404)** | You followed a link that does not exist. Go back to the Dashboard. |
| **Server error (500)** | Something broke inside the application. This is a bug — please report it. |
| **Forbidden (403)** | The application blocked your action for security reasons. Try logging in again. |

### For developers: how the QA environment works

The testing environment mirrors production:

| Setting | Value | Why |
|---------|-------|-----|
| `DEBUG` | `False` | Hides technical details from error pages, same as production |
| `DJANGO_ENV` | `testing` | Like production (collectstatic, Gunicorn, secure cookies) but enables the `/testing/` dashboard |
| Server | Gunicorn (2 workers) | Same as production (not Django's development server) |
| HTTPS cookies | `Secure=True`, session `SameSite=Lax`, CSRF `SameSite=Strict` | Same cookie policy as production |
| HTTPS | Via Nginx reverse proxy (local) or Cloud Run (GCP) | See [HTTPS.md](docs/HTTPS.md) for full setup guide |
| `SECURE_PROXY_SSL_HEADER` | Trusts `X-Forwarded-Proto` from reverse proxy | Enables Django to detect HTTPS behind Nginx/Cloud Run |
| Database | PostgreSQL 16 (separate volume) | Isolated from the development database |
| Login | Teacher email + password via `auth.User`; seeded by `manage.py seed_teachers` from `TEACHER_SEED_<N>_*` env vars | Same login path as production — exercises the real Teacher auth flow |
| Password reset | `/password-reset/...` (public, branded templates) | Lets QA teachers without an initial password activate via email |
| Admin panel | `/admin/` — same Teacher session (admin teachers only) | Django admin for inspecting raw data; non-admin teachers don't see it |

**Configuration files:**

| File | Purpose |
|------|---------|
| `.env.testing` | Self-contained env file for QA — Django, database, security flags, Gmail SMTP, and the `TEACHER_SEED_<N>_*` blocks. Rename to `.env` before bringing the stack up. |
| `docker-compose.testing.yml` | Docker overlay that switches `web` to Gunicorn and isolates `db` into a separate volume (`testing_postgres_data`). |
| `seed_testdata` command | Populates the database with realistic fake data |
| `seed_teachers` command | Idempotently creates Teacher rows + linked `auth.User` accounts from `TEACHER_SEED_*` env vars; runs automatically on container start |
| `HTTPS.md` | Full guide for HTTPS setup with Docker (Nginx + self-signed cert) and GCP Cloud Run |
| `/testing/` | In-app QA dashboard with project info, seeding, backlog, and error reporting toggle |
| `core/decorators.py` | `qa_access_required` decorator — reusable access gate for QA-only views |

#### Access control for `/testing/`

The testing dashboard and all its API endpoints are protected by three conditions that must **all** be true:

| Condition | Setting | Where it's checked |
|---|---|---|
| Environment is `testing` | `DJANGO_ENV=testing` | `settings.IS_TESTING_ENV` |
| Debug is off | `DJANGO_DEBUG=False` | `settings.IS_TESTING_ENV` |
| Request is a logged-in Teacher | linked `Teacher` on the session user | `core/decorators.py` (`_request_teacher`) |

If any condition fails, the page returns **404 Not Found** (not 403) so the URL appears not to exist. The sidebar icon is also hidden — controlled by the `show_testing_tools` context variable injected by `core/context_processors.py`.

This means:
- In **development** (`DEBUG=True`): the page doesn't exist, no sidebar icon.
- In **production** (`DJANGO_ENV=production`): the page doesn't exist, no sidebar icon.
- In **testing** with a **non-Teacher session**: the page doesn't exist, no sidebar icon.
- In **testing** logged in as **any Teacher** (admin or not): full access, sidebar icon visible.

Access is granted to every seeded Teacher account (`TEACHER_SEED_*`) — no dedicated QA user is needed. Non-admin teachers reach it because `testing_tools` and the QA API endpoints are on the non-admin whitelist in `core/middleware.py`.

**Running locally (for developers):**

```bash
# Activate the QA env file
mv .env.testing .env

# Start the QA stack (Gunicorn + isolated DB volume)
docker compose -f docker-compose.yml -f docker-compose.testing.yml up -d --build

# Populate with test data (students, parents, payments, etc.)
docker compose exec web python project/manage.py seed_testdata

# Wipe everything and re-seed from scratch
docker compose exec web python project/manage.py seed_testdata --reset

# View logs
docker compose logs -f

# Stop the stack (keeps the testing_postgres_data volume)
docker compose -f docker-compose.yml -f docker-compose.testing.yml down

# Switch back to dev
mv .env .env.testing && mv .env.development .env
```

The `seed_testdata` command creates:
- 3 teachers, 5 groups
- 6 parents, 12 child students, 3 adult students, 1 inactive student
- Active enrollments with monthly and quarterly payment plans
- Payments in various states (completed, pending, overdue)
- Schedule slots, todo items, and history log entries

Use `--reset` to wipe and re-seed, or `--small` for a minimal dataset (6 children only).

> **Deploying the QA environment** — see [DEPLOYMENT.md](DEPLOYMENT.md) for the full GCP plan. Testing runs on a Compute Engine e2-micro (free tier) with Docker Compose, while production uses Cloud Run + Cloud SQL.

---

## CI/CD & GitHub Actions

The project runs a fully automated CI/CD pipeline on GitHub Actions. Every push is tested, every merge is audited, and production is reached only through a protected pull request. The full configuration reference is in [docs/GITHUB.md](docs/GITHUB.md) — this section is the overview.

### Pipeline Overview

```text
Push to development
        │
        ▼
CI runs (lint + typecheck + tests) + CodeQL
        │
        │  hourly cron
        ▼
Auto-merge check
  • development ahead of testing?
  • last commit ≥ 24 h old?
  • CI passing on that commit?
  • version bumped in pyproject.toml (dev > testing)?
        │ all yes
        ▼
git merge development → testing
(commit: "YYYY-MM-DD - <last commit message>")
        │
        ├── CI re-runs on testing
        └── PR created: testing → main
        │
        ▼
Email to owners (OWNER_EMAILS)
        │
        ▼
Manual review + Code Owner approval
        │
        ▼
Merge to main (protected — all checks required)
        │
        ▼
Email to hellofiveaday@gmail.com
(production deploy ready)
```

### Branch Strategy

| Branch        | Purpose                                  | Protected                | Direct push              |
|---------------|------------------------------------------|--------------------------|--------------------------|
| `main`        | Production. Every commit is deployable.  | Full protection          | No (PR + review only)    |
| `testing`     | Staging. Auto-merged from development.   | Minimal (no force/delete)| Only from auto-merge flow|
| `development` | Active development. Day-to-day work.     | None                     | Yes                      |

Feature branches off `development` are welcome for non-trivial work, but the expected flow is: work on `development` → wait 24 h → auto-promoted to `testing` → manual merge to `main`.

### Workflows

| Workflow | File | Triggers | Purpose |
|----------|------|----------|---------|
| **CI** | [`ci.yml`](.github/workflows/ci.yml) | Push to `development`/`testing`/`main`; PRs to `testing`/`main` | Six jobs — **Lint** (Ruff + Bandit + pip-audit + Hadolint), **Type check** (mypy), **Tests** (pytest + PostgreSQL 16 + Codecov), **Docker build** (validates Dockerfile), **Trivy** (filesystem CVE scan → Security tab), **Docker publish** (GHCR push + image scan, on `main`/`testing` only) |
| **Auto-merge** | [`auto-merge.yml`](.github/workflows/auto-merge.yml) | Hourly cron + manual dispatch | Merges `development` → `testing` when conditions pass, creates PR to `main`, emails owners |
| **CodeQL** | [`codeql.yml`](.github/workflows/codeql.yml) | Push to `main`/`testing`/`development`; PRs to `main`; Monday 04:30 UTC | Python static security analysis (OWASP Top 10, Django-specific queries) |
| **Notify production** | [`notify-production.yml`](.github/workflows/notify-production.yml) | Push to `main` | Emails `hellofiveaday@gmail.com` with commit info and `gcloud` deploy instructions |
| **Dependabot auto-merge** | [`dependabot-auto-merge.yml`](.github/workflows/dependabot-auto-merge.yml) | Pull request (Dependabot only) | Enables auto-merge for minor/patch Dependabot PRs once CI passes |
| **Dependency review** | [`dependency-review.yml`](.github/workflows/dependency-review.yml) | Pull request | Blocks PRs that introduce a HIGH/CRITICAL CVE dependency |
| **OSSF Scorecard** | [`scorecard.yml`](.github/workflows/scorecard.yml) | Push to `main`; weekly Monday 06:00 UTC; branch protection rule changes | Grades supply-chain security posture; uploads SARIF to GitHub Security tab |
| **Dependabot** | [`dependabot.yml`](.github/dependabot.yml) | Weekly (Mondays 08:00 Madrid) | Grouped Python and GitHub Actions updates targeting `development` |

Concurrent CI runs on the same branch cancel each other automatically — new pushes always produce a fresh run.

### Automated Flows

**1. You push to `development`**

- CI triggers immediately (lint, typecheck, tests run in parallel, ~2-4 min)
- CodeQL triggers immediately (weekly scan also runs independently)
- The hourly auto-merge cron promotes to `testing` only when **all four** conditions hold: dev is ahead of testing, the last commit is ≥ 24 h old, CI is green, **and the version in `pyproject.toml` has been bumped** (strictly higher than `testing`'s version). Without a version bump the merge is skipped even with 24 h of new commits on dev — run `make pc-run` (answer yes) or `make version x.y.z` before the next tick to unlock it.

**2. Auto-merge fires**

- Creates a `--no-ff` merge commit on `testing` titled `YYYY-MM-DD - <your last commit message>`
- Pushes to `testing` (which triggers CI on `testing`)
- **Creates and pushes an annotated staging tag `testing-vX.Y.Z`** on the new testing merge commit
- Opens PR `testing → main` if one is not already open (title matches the merge commit)
- Sends an HTML email to `TESTING_NOTIFY_EMAILS` (falling back to `OWNER_EMAILS`) with version bump, staging tag, and a "Review PR" button

**2b. You merge the PR → release tag on main**

- `notify-production.yml` reads `version` from `pyproject.toml` on `main`'s new HEAD
- **Creates and pushes an annotated release tag `vX.Y.Z`** on that commit (skipped if tag already exists)
- Sends an HTML email to `hellofiveaday@gmail.com` with the release tag and `gcloud` deploy steps

The two tag namespaces (`testing-vX.Y.Z` and `vX.Y.Z`) are fully independent — the `testing → main` PR can use any merge strategy (merge commit, squash, or rebase) because the release tag is derived from `pyproject.toml`, not from commit SHA continuity.

**3. You review and merge the PR**

- All required checks must pass (Lint, Type check, Tests, CodeQL alerts, Code Owner approval)
- You cannot approve your own PR — the second owner account approves
- On merge, `main` is updated

**4. Production notification fires**

- `notify-production.yml` sends an email to `hellofiveaday@gmail.com` (plus `SUPPORT_EMAIL` when set)
- Email contains commit info, file-change summary, and the exact `gcloud` commands to deploy to Cloud Run

### Branch Protection — `main`

Configure at **Settings → Branches → Add ruleset**, target `main`:

**Required status checks** (names must match CI job names exactly):

| Check | Workflow |
|-------|----------|
| `Lint` | ci.yml |
| `Type check` | ci.yml |
| `Tests` | ci.yml |
| `Analyze Python` | codeql.yml |

**Protection rules** (every item below enabled):

| Rule | Setting |
|------|---------|
| Require a pull request before merging | ✓ |
| Required approvals | **1** (higher if you add collaborators) |
| Dismiss stale reviews when new commits are pushed | ✓ |
| Require review from Code Owners | ✓ |
| Require status checks to pass | ✓ |
| Require branches to be up to date before merging | ✓ |
| Require conversation resolution before merging | ✓ |
| Require signed commits | ✓ (strongly recommended for a public repo) |
| Require linear history | ✓ (enforces squash/rebase merges) |
| Restrict who can push to matching branches | ✓ |
| Do not allow bypassing the above settings | ✓ (admins follow the same rules) |
| Allow force pushes | ✗ |
| Allow deletions | ✗ |

### Branch Protection — `testing`

`testing` needs direct pushes from the auto-merge workflow, so PR requirements are **not** enforced. Apply only safety rails:

| Rule | Setting |
|------|---------|
| Require a pull request before merging | ✗ |
| Allow force pushes | ✗ |
| Allow deletions | ✗ |
| Require status checks to pass (optional) | ✓ — lets CI block a broken auto-merge from polluting `testing` further |

### Public Repository Hardening

Because this repository is **public**, extra care is taken to prevent accidental secret leaks, abuse of the CI, and unreviewed contributions:

| Control | Where | Why |
|---------|-------|-----|
| **GitHub Secret Scanning** | Settings → Code security | Free for public repos — detects committed secrets across history |
| **Push Protection** | Settings → Code security | Free for public repos — blocks pushes that contain secrets before they land |
| **CodeQL** | `codeql.yml` + Settings → Code security | Free for public repos — weekly security analysis |
| **OSSF Scorecard** | `scorecard.yml` + Settings → Code security | Free for public repos — weekly supply-chain security grading (branch protection, dependency pinning, CI, secret scanning) |
| **Dependency review** | `dependency-review.yml` | Blocks PRs that introduce a new HIGH/CRITICAL CVE dependency — catches supply-chain attacks before they merge |
| **Dependabot alerts + security updates** | Settings → Code security | Free for public repos — fixes known CVEs in dependencies |
| **Require 2FA for all contributors** | Organization settings (if in an org) | Prevents compromised account pushes |
| **Restrict fork PRs from running CI with secrets** | Settings → Actions → Fork PR workflows: require approval for first-time contributors | Prevents secret exfiltration via malicious PRs from forks |
| **Actions allow-list** | Settings → Actions → Allow specific actions | Prevents supply-chain attacks — pin to verified creators only |
| **Workflow permissions default: read-only** | Settings → Actions → Workflow permissions | Individual workflows explicitly request `write` where needed |
| **Block workflows from approving PRs** | Settings → Actions → Allow GitHub Actions to create and approve pull requests: **only allow create, not approve** | Humans must approve, even automated PRs |
| **SECURITY.md** | Root of the repo | Public disclosure policy so researchers know how to report vulnerabilities privately |
| **License file** | Root of the repo | Required for a public repo — defines what others can legally do with the code |

The `.env` file is gitignored and **never** committed. Production secrets live in GCP Secret Manager (see [DEPLOYMENT.md](DEPLOYMENT.md)), not in the repository or in GitHub Secrets. GitHub Secrets are used only for CI operations (sending notification emails, uploading coverage).

### Required GitHub Secrets

Configure at **Settings → Secrets and variables → Actions**:

| Secret | Required by | Purpose |
|--------|-------------|---------|
| `GH_PAT` | auto-merge.yml | Fine-grained Personal Access Token. Pushes to `testing` and creates PRs *while triggering downstream CI* (which the default `GITHUB_TOKEN` cannot do). Permissions: Contents RW, Pull requests RW, Checks R, Metadata R |
| `EMAIL_HOST_USER` | auto-merge.yml, notify-production.yml | Gmail address used to send notification emails |
| `EMAIL_SECRET` | auto-merge.yml, notify-production.yml | Gmail App Password — can be the same one the application uses for transactional email |
| `OWNER_EMAILS` | auto-merge.yml | Comma-separated fallback recipient list for the `development → testing` merge notification (used when `TESTING_NOTIFY_EMAILS` is unset) |
| `TESTING_NOTIFY_EMAILS` | auto-merge.yml | Comma-separated recipients for the `development → testing` deploy email — support + the two admin teachers. Preferred over `OWNER_EMAILS` |
| `TESTING_URL` | auto-merge.yml | Base URL of the testing environment, used for the "Open testing environment" button (falls back to the testing VM IP) |
| `SUPPORT_EMAIL` | notify-production.yml | Support address added (alongside `hellofiveaday@gmail.com`) to the production deploy email |
| `PRODUCTION_URL` | notify-production.yml | Base URL of production — adds the "Abrir producción" button to the production email. **Set** (v1.14.7) to `https://fiveaday-332600671945.europe-southwest1.run.app` |
| `CODECOV_TOKEN` | ci.yml | Optional — only needed for private repos. Public repos push coverage anonymously |

**Rotate `GH_PAT` annually.** Without it, the auto-merge falls back to the default `GITHUB_TOKEN`, which cannot trigger CI on PRs it creates — breaking the pipeline silently.

### Email Notifications

| Event | Recipient | Sent by |
|-------|-----------|---------|
| `development → testing` merged + PR opened to `main` | `TESTING_NOTIFY_EMAILS`, falling back to `OWNER_EMAILS` | auto-merge.yml |
| New commit on `main` (production ready to deploy) | `hellofiveaday@gmail.com` (hardcoded) + `SUPPORT_EMAIL` when set | notify-production.yml |

Both use Gmail SMTP via the `dawidd6/action-send-mail@v18` action. Emails include HTML formatting, links to the commit/PR, and actionable next steps.

### Dependabot

Dependabot opens **weekly PRs on `development`** (Mondays, 08:00 Europe/Madrid) for:

- **Python packages** — minor and patch updates grouped into a single PR. Django major version bumps are intentionally ignored (require manual upgrade planning).
- **GitHub Actions** — updates to `actions/*`, `astral-sh/setup-uv`, `dawidd6/action-send-mail`, etc.

PRs are labelled `dependencies` + `python` or `github-actions` for easy filtering. The normal 24 h cycle carries merged updates to `testing` and then to `main`.

### CodeQL Security Scanning

Runs on every push and PR to `main`, plus a full scan every Monday at 04:30 UTC. Uses the `security-and-quality` query suite — covers OWASP Top 10, CWE Top 25, and Django-specific queries (SQL injection, path traversal, hardcoded credentials, insecure deserialization, etc.).

Results appear in **Security → Code scanning alerts**. A new alert on `main` does not auto-block future merges unless branch protection is configured to require the CodeQL check.

---

## Contributing

### Development Workflow

```bash
# First-time setup
uv sync --no-install-project   # Install all dependencies (UV — see docs/UV.md)
make pre-commit-install        # Install the git pre-commit hook
make up                        # Start Docker (PostgreSQL + Redis + Django + Celery)
```

1. Work on `development` (or a short-lived branch off `development`)
2. Make changes following the conventions below
3. Run `make pc-run` — Ruff + mypy + bandit all pass, offers to auto-bump the patch version on success, and auto-stages `uv.lock` if regenerated
4. Run `make test` — all 1,214 tests must pass (PostgreSQL via Docker, parallel, with coverage)
5. `git commit` with a message like `v1.14.7 — Short description` (version first, em dash — matches every other release commit in the project)
6. `git push origin development`
7. CI runs automatically on your push (see [CI/CD](#cicd--github-actions))
8. ~24 h later, the auto-merge pipeline promotes your commit to `testing` and opens a PR to `main` for your review

Pre-commit hooks run **Ruff** (lint + format), **mypy** (type checking), and **bandit** (security) automatically on every `git commit`. If a hook modifies files (e.g. mypy regenerates `uv.lock`), the commit aborts — running `make pc-run` once resolves this by staging the regenerated lock file.

### Make Commands (Developer Tooling)

| Tool | Purpose | Command |
|------|---------|---------|
| **UV** | Dependency management | `uv sync`, `uv add`, `uv lock` |
| **Ruff** | Lint + format | `make lint`, `make format` |
| **mypy** | Type checking | `make mypy` |
| **bandit** | Security linting | `make bandit` |
| **pip-audit** | Dependency CVE scanning | `make audit` |
| **pytest-xdist** | Parallel test execution | Built into `make test` (`-n auto`) |
| **pytest-randomly** | Randomized test ordering | Built into `make test` (seed printed) |
| **pytest-cov** | Coverage reporting + badge | `make test`, `make coverage-badge` |
| **pre-commit** | Git hooks: ruff, ruff-format, mypy, bandit | `make pre-commit-install` (first-time), `make pc-run` (dry-run all hooks + auto bump) |
| **make version** | Bump the version in all four places — `pyproject.toml`, `settings.py` (`APP_VERSION`), the README badge URL, and `uv.lock` | `make version x.y.z` (positional, with `y/N` confirmation); bare `make version` prints the current values and warns on drift |

All tools are configured in `pyproject.toml` and installed as dev dependencies via `uv sync`.

### Code Conventions

| Area | Convention |
|------|-----------|
| **Language** | Code in English, UI/templates in Spanish, comments mixed |
| **Models** | Explicit `db_table`, `created_at`/`updated_at` timestamps, BigAutoField PKs |
| **Views** | CBVs for CRUD, FBVs for everything else. AJAX returns `{"success": bool, ...}` |
| **Forms** | ModelForms for data entry. Business logic delegates to services. |
| **Templates** | Extend `base.html`. Blocks: `title`, `page_title`, `content`, `extra_js` |
| **JS** | External files in `core/static/js/`. Django data via `data-*` attrs or `window.CONFIG` |
| **Services** | Pure business logic in `billing/services/`. No request/response objects. |
| **Tests** | pytest with fixtures in `conftest.py`. `authenticated_client` for view tests. |
| **Imports** | Always explicit — no `from app.models import *` |
| **Pricing** | Always from `SiteConfiguration.get_config()`, never hardcoded |
| **Template names** | Always in English (e.g., `enrollment_child.html`, not `matricula_niño.html`) |

### Adding a Feature

1. **Model** → correct app (students/billing/core), explicit `db_table`
2. **Service** → `billing/services/` or new service if it has business logic
3. **View** → appropriate `core/views/` module, add to `__init__.py` re-exports
4. **URL** → correct app's `urls.py`
5. **Template** → `core/templates/`, extend `base.html`
6. **Tests** → fixtures in `conftest.py`, tests in correct test file
7. **Admin** → correct app's `admin.py`
8. **Docs** → update this README, app README, CLAUDE.md if needed

---

## License

Private project — all rights reserved.

Developed for Five a Day English Academy, Albacete, Spain.
