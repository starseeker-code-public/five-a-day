# comms — Communications

The `comms` app owns all email sending logic: the EmailService class, 12+ convenience email functions, Celery async tasks, and management commands for sending emails.

**No database models** — all state lives in the other apps. Comms is purely a service layer for communications.

## Services

### EmailService (`comms/services/email_service.py`)

Generic email sending service with HTML template rendering and inline images.

- `send_email(template_name, recipients, subject, context, ...)` — renders a Django template and sends via SMTP
- `send_bulk_emails(template_name, emails_data, ...)` — sends multiple emails with the same template
- `email_service` — singleton instance used throughout the project

Templates live in `core/templates/emails/` and extend `emails/base_email.html`. There are currently **14 email templates**: `happy_birthday`, `welcome_student`, `enrollment_child`, `enrollment_adult`, `fun_friday`, `payment_reminder`, `receipt_quarterly_child`, `receipt_adult`, `receipt_enrollment`, `vacation_closure`, `tax_certificate`, `monthly_report`, `newsletter`, plus the shared `base_email`.

### Email Functions (`comms/services/email_functions.py`)

Convenience functions for each email type. Each wraps `email_service.send_email()` with template-specific parameters:

| Function | Template | Trigger |
| -------- | -------- | ------- |
| `send_birthday_email` | `happy_birthday` | Daily cron / manual |
| `send_welcome_email` | `welcome_student` | On student creation |
| `send_enrollment_confirmation_email` | `enrollment_child` / `enrollment_adult` | On enrollment |
| `send_fun_friday_email` | `fun_friday` | Weekly manual |
| `send_payment_reminder_email` | `payment_reminder` | Monthly manual |
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
| `send_welcome_email_task` | Async welcome email | On student creation |
| `send_birthday_email_task` | Individual birthday email | Called by batch task |
| `send_birthday_emails_task` | Daily birthday batch | Celery Beat (8:00 AM) |
| `send_payment_reminders` | Weekly payment reminder batch | Celery Beat |
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

10 URL patterns for the email app form views (`apps/`, `apps/fun-friday/`, `apps/payment-reminder/`, etc.). Views are imported from `core.views.app_forms`.

## Tests

Tests for comms services live in `project/tests/`:

| File | What it tests |
| ---- | ------------- |
| `test_email_service.py` | `EmailService` — basic send, multiple recipients, CC/BCC, attachments, fail_silently, bulk sends, bad template handling. Uses `django.core.mail.outbox` (locmem backend). |
| `test_email_functions.py` | All convenience functions in `email_functions.py` — correct template, subject, context, and fail_silently for each function |

Run with `make test` (requires Docker + PostgreSQL running).

## Cross-App Communication

- **Depends on**: students (Student, Parent for recipient resolution), billing (Payment for tax certificates)
- **Depended on by**: core views (student creation triggers welcome email task, app form views send emails)
- **Imported by**: `core/views/students.py` imports `comms.tasks.send_welcome_email_task`; `core/views/app_forms.py` imports email functions and email_service
