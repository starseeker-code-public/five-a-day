# comms — Communications

The `comms` app owns all outbound communication: the EmailService class, the Twilio SmsService, 10 convenience email functions, 12 Celery async tasks, and management commands for sending emails and for wrapping the Beat tasks.

**No database models** — all state lives in the other apps. Comms is purely a service layer for communications.

## Services

### EmailService (`comms/services/email_service.py`)

Generic email sending service with HTML template rendering and inline images.

- `send_email(template_name, recipients, subject, context, ...)` — renders a Django template and sends via SMTP
- `send_bulk_emails(template_name, emails_data, ...)` — sends multiple emails with the same template
- `email_service` — singleton instance used throughout the project

Templates live in `core/templates/emails/` and extend `emails/base_email.html`. There are currently **18 content templates** (19 files including the shared base): `happy_birthday`, `welcome_student`, `enrollment_child`, `enrollment_adult`, `fun_friday`, `payment_reminder`, `payment_reminder_simple`, `payment_receipt`, `receipt_quarterly_child`, `receipt_adult`, `receipt_enrollment`, `vacation_closure`, `tax_certificate`, `monthly_report`, `admin_monthly_report`, `newsletter`, `parent_set_password`, `password_reset`, plus the shared `base_email`.

**Every content template must define its own `{% block title %}`** (v1.20.0). `base_email.html` supplies a generic "Five a Day" fallback and 11 of the 18 were silently taking it, so the document title bore no relation to the subject line. Give a new template a title matching its `subject=`, in the shared `"<asunto> · Five a Day"` shape.

### SmsService (`comms/services/sms_service.py`)

Twilio SMS sender (v1.8). The `twilio` client is imported lazily so the app runs fine without it.

- `is_configured()` — true only when all three of `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` are set
- `send(to, body)` — returns an `SmsResult`; the destination number and any Twilio error are passed through `safe_log()` in the log record *and* in `SmsResult.error`, because callers surface that text in responses
- `send_to_parent(parent, body)` — **strictly opt-in**: only parents with `sms_opt_in=True` receive anything
- `get_sms_service()` — singleton accessor

### Email Functions (`comms/services/email_functions.py`)

Convenience functions for each email type. Each wraps `email_service.send_email()` with template-specific parameters:

| Function | Template | Trigger |
| -------- | -------- | ------- |
| `send_welcome_email` | `welcome_student` | On student creation |
| `send_enrollment_confirmation_email` | `enrollment_child` / `enrollment_adult` | On enrollment |
| `send_fun_friday_email` | `fun_friday` | Weekly manual |
| `send_payment_reminder_email` | `payment_reminder` | Monthly manual — takes five fee figures (`full_time_fee`, `part_time_fee`, `adult_fee`, `quarterly_fee`, `sibling_full_time_fee`). Any left `None` are filled from `PricingService.payment_reminder_fees()`, so no caller can send the tariff table blank (v1.20.0) |
| `send_quarterly_receipt_email` | `receipt_quarterly_child` | Quarterly manual |
| `send_vacation_closure_email` | `vacation_closure` | Manual |
| `send_tax_certificate_email` | `tax_certificate` | Yearly (April) |
| `send_all_tax_certificates` | (iterates parents) | Yearly batch |
| `send_monthly_report` | `monthly_report` | Monthly manual |
| `generate_tax_certificate_pdf` | (HTML to PDF) | Called by tax certificate |

The **newsletter** and **enrollment receipt** templates do not have dedicated convenience functions — they are triggered directly from the `/apps/newsletter/` and receipt form views in `core/views/app_forms.py`, which call `email_service.send_email()` inline with per-recipient context.

## Logging Helper (`comms/log_safe.py`)

`safe_log(value, max_len=200)` — single-line, length-capped rendering for anything
user-controlled that is about to be formatted into a log record or handed back to a
caller. Used by `email_service` (the email template name) and `sms_service` (the
destination number and the Twilio error, in the log *and* in `SmsResult.error`,
which callers surface in responses).

It is a deliberate near-copy of `core/log_safe.py` rather than an import: `comms`
must not depend on `core` (see the dependency flow in [CLAUDE.md](../../CLAUDE.md)),
and duplicating ten stdlib-only lines is cheaper than inverting that.

**Caveat (v1.14.6):** this makes the code safe but does *not* clear CodeQL's
`py/log-injection`, which treats `str.replace` as taint-preserving. Where a value
can be coerced (an integer id) or simply left out of the record, prefer that.

## Celery Tasks (`comms/tasks.py`)

All tasks have retry logic (3 retries, exponential backoff):

| Task | Purpose | Trigger |
| ---- | ------- | ------- |
| `send_welcome_email_task` | Async welcome email (includes the group's timetable). Reports the payment modality in Spanish, and "Especial" for a `special` matrícula — its cadence is whatever was agreed with the family, not a standard one (v1.20.0) | On student creation, fired `on_commit` |
| `send_birthday_email_task` | Individual birthday email — every parent with an address, adult students themselves. v1.26.8 attaches `happy-birthday.png` as a **`cid:` inline part**; the template's `<img src="cid:birthday_image">` had nothing attached, so every birthday email ever sent rendered a broken image | Called by batch task. Production runs `CELERY_TASK_ALWAYS_EAGER=True`, so this executes **inline**: 2 queries per student since v1.26.1, down from 3 (a `prefetch_related` that its own `exclude()` discarded). Fan-out task costs are invisible in dev, where subtask queries land in the worker |
| `send_birthday_emails_task` | Daily birthday batch — goes to **all** of a student's parents, dated with `localdate()` | Celery Beat (daily 08:00) / `send_birthday_emails` command |
| `send_payment_reminders` | Weekly payment reminder batch, deduped against the SMS channel | Celery Beat (Mondays 09:00) / `send_payment_reminders` command |
| `send_payment_reminder_sms_task` | Twilio SMS reminder for one payment — opt-in parents only (v1.8) | Called from the reminder batch |
| `send_monthly_report_task` | Admin monthly report. With no explicit recipient it now (v1.26.8) goes to **both** `SUPPORT_EMAIL` and `DEFAULT_FROM_EMAIL`, deduped — the academy reads it in two inboxes — and is skipped only when neither is set. `--recipient` still overrides both | Celery Beat (28th, 20:00) / `send_monthly_report` command |
| `send_parent_temporary_password_task` | Generates, hashes onto `Parent.temporary_password` and emails a one-off portal password — the invitation when the record is created, and the recovery from `¿Has olvidado tu contraseña?` (`reset=True` swaps the copy). The plaintext is generated **inside** the task, never passed as an argument: task arguments are serialised into the broker and printed in task logs (v1.9, reworked v1.27) | `ParentCreateView`, parent-portal recovery form |
| `send_payment_receipt_email_task` | Emails a receipt PDF for a completed payment (v1.11) | Payment completion / Stripe webhook |
| `send_generic_email_task` | Generic email dispatcher | Manual |
| `send_enrollment_confirmation_task` | Enrollment confirmation with attachments (uses `student.gender` field) | On enrollment |
| `send_fun_friday_emails_task` | Fun Friday announcement to all parents (immediate) | Direct/manual sends |
| `send_due_fun_friday_emails_task` | Drains due `FunFridayScheduledSend` rows (idempotent — rows are marked `sent_at`) | Celery Beat (daily 14:30) / `send_due_fun_friday_emails` command |

Without Redis, Celery runs in eager mode (synchronous, same process).

> **Fun Friday scheduling** does NOT use `apply_async(eta=…)` — the ETA is silently ignored
> in eager mode (production has no worker). The form persists a `core.FunFridayScheduledSend`
> row scheduled for Monday 14:30 of the event week; the drain task sends due rows on time.

## Management Commands

### `send_email`

```bash
python manage.py send_email --template happy_birthday --test
python manage.py send_email --fun-friday --activity "Zumba" --date 2025-10-10 --time 17:00-18:30
python manage.py send_email --payment-reminder --month octubre
python manage.py send_email --tax-certificate --year 2024
```

### `test_all_emails`

```bash
python manage.py test_all_emails                     # Send one test of each email template
python manage.py test_all_emails --only fun_friday,birthday
python manage.py test_all_emails --list              # List available templates
python manage.py test_all_emails --to admin@test.com
```

### Beat-task wrappers (for external schedulers)

Production runs no Celery Beat process (Cloud Run) — each periodic task has a thin
command wrapper that runs it synchronously via `.apply()`, so Cloud Scheduler → Cloud
Run Jobs (or plain cron) can trigger it. See `DEPLOYMENT.md` for the schedule table.

```bash
python manage.py send_birthday_emails            # daily 08:00 batch
python manage.py send_payment_reminders          # weekly Monday 09:00 batch
python manage.py send_monthly_report             # 28th 20:00 (--recipient overrides SUPPORT_EMAIL)
python manage.py send_due_fun_friday_emails      # daily 14:30 — drains due FunFridayScheduledSend rows
```

## URL Patterns (comms/urls.py)

11 URL patterns for the email app form views (`apps/`, `apps/fun-friday/`, `apps/payment-reminder/`, etc.). Views are imported from `core.views.app_forms`.

## Tests

Tests for comms services live in `project/tests/`:

| File | What it tests |
| ---- | ------------- |
| `test_email_service.py` | `EmailService` — basic send, multiple recipients, CC/BCC, attachments, fail_silently, bulk sends, bad template handling. Uses `django.core.mail.outbox` (locmem backend). |
| `test_email_functions.py` | All convenience functions in `email_functions.py` — correct template, subject, context, and fail_silently for each function |
| `test_email_service_year.py` | Regression: the `year` context value used to be hard-coded to 2025 |
| `test_tasks.py` | The core email tasks called synchronously with `email_service` mocked |
| `test_new_email_tasks.py` | `send_parent_temporary_password_task` (v1.9, reworked v1.27 — both the invitation and the reset flavour, and the plaintext never crossing the task boundary) + `send_payment_receipt_email_task` (v1.11) |
| `test_sms_service.py` / `test_sms_tasks.py` | `SmsService` configuration/send/opt-in gate, and the SMS reminder task |
| `test_email_bug_hunt_fixes.py` | Round-2 email regressions — templates, image guard, `on_commit`, all-parents birthday, timezone |
| `test_fun_friday_scheduling.py` | `FunFridayScheduledSend.is_due` + the idempotent drain task |
| `test_beat_commands.py` | The Beat-task management-command wrappers |

Run with `make test` (requires Docker + PostgreSQL running).

## Cross-App Communication

- **Depends on**: students (Student, Parent for recipient resolution), billing (Payment for tax certificates)
- **Depended on by**: core views (student creation triggers welcome email task, app form views send emails)
- **Imported by**: `core/views/students.py` imports `comms.tasks.send_welcome_email_task`; `core/views/app_forms.py` imports email functions and email_service

### Known debt — `comms` reaches up into `core`

Two lazy, function-body imports in `comms/tasks.py` reverse the documented dependency flow:

- `core.schedule_utils.get_group_schedule_lines` — the welcome email prints the group's timetable, and `ScheduleSlot` is a `core` model
- `core.models.FunFridayScheduledSend` — the drain task consumes a `core` model

Because both are inside function bodies there is no import cycle today; the cost is coupling.
The fix for the first is to compute `schedule_lines` in the `core` request path and pass them
into `send_welcome_email_task`; the second probably wants the model moved to a neutral layer.
Deferred deliberately in v1.14.6 — fixing only one would leave the codebase inconsistent with
itself. (`comms/urls.py` importing `core.views` is a separate, deliberate choice and not part
of this.) See [CLAUDE.md](../../CLAUDE.md).
