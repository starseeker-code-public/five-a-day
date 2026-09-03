# comms — Communications

The `comms` app owns all outbound communication: the EmailService class, the Twilio SmsService, 10 convenience email functions, 11 Celery async tasks, and management commands for sending emails and for wrapping the Beat tasks.

**No database models** — all state lives in the other apps. Comms is purely a service layer for communications.

## Who a mass mail reaches (v1.27.1)

Every batch send in the project — the ten `/apps/` mail forms, the Beat tasks and the `send_email`
command — resolves its recipients the same way now. The rules, and the bug each one closes:

- **Waiting-list families are excluded** (`children__is_waiting=False`). A waiting-list entry taken
  over the phone has no `Parent` row at all (its contact lives on `Student.waiting_contact_*`), so
  the omission looked harmless — but `add_to_waiting_list` moves an **enrolled** student onto the
  list by cancelling the enrollment and leaving `active=True`, and that student keeps their parents.
  Filtering on `active` alone therefore sent payment reminders, receipts and every announcement to
  families whose child is not enrolled and owes nothing.
- **Addresses are deduplicated case-insensitively.** `Parent.email` is *not* unique — a couple
  sharing one mailbox is two legitimate rows — so every send delivered one copy per **row**.
- **Parents with no address no longer inflate the count.** The six `parent_count` figures lacked an
  `if p.email` filter, so the page overstated reachable recipients by however many families have no
  address on file; and the counter, the sent set and the success banner were three different
  numbers.
- **One SMTP connection per batch** (`EmailService.open_connection()`), instead of a
  TCP+TLS+AUTH handshake per recipient. At roster scale that handshake *is* the request.
- **A connection failure reports a tally, not a 500.** `open()` is not `fail_silently`, so an
  unwrapped open propagated out of the view; the operator now gets a Spanish error and every
  message counts as unsent, with a `HistoryLog` entry either way.

A sixth fix has no rule of its own: `_emailable_parent()` is now the **one** resolution of "which
guardian does a single-student email go to?". `enrollment_form`'s preview did
`parents.exclude(...).first()` while its send did `parents.exclude(...).order_by("id").first()` —
both happen to sort by pk today, so the preview would have started showing a different guardian than
the one who receives the mail the moment either side grew an ordering. It also honours
`_EMAILABLE_PARENTS_PREFETCH`, since `.filter()` / `.first()` on a prefetched related manager throws
the prefetch away (measured: 365 queries for 120 parents).

The canonical implementation is `core.views.app_forms` (`_recipient_filters`,
`_parent_recipients`, `_mass_mail_parents`, `_dedupe_emails`, `_emailable_parent`, `_mass_send`);
`comms/tasks.py` and `comms/management/commands/send_email.py` carry deliberate two-line twins
whose docstrings name their counterpart, because `comms` importing `core.views` would invert the
documented dependency flow.

**The tax certificate is the one deliberate exception** — see `send_all_tax_certificates` below.

## Services

### EmailService (`comms/services/email_service.py`)

Generic email sending service with HTML template rendering and inline images.

- `send_email(template_name, recipients, subject, context, ..., connection=None, fail_silently=...)` — renders a Django template and sends via SMTP
- `send_bulk_emails(template_name, emails_data, ...)` — sends multiple emails with the same template
- `open_connection()` — a single reusable SMTP connection for a batch of sends (see below)
- `email_service` — singleton instance used throughout the project

> **`fail_silently` and `connection` are mutually exclusive — and getting that wrong broke EVERY
> mass send** (v1.27.1). Django refuses both at once (*"fail_silently cannot be used with a
> connection. Pass fail_silently to get_connection() instead."*), so `send_email` passes
> `fail_silently` to `send()` **only when it is opening its own connection**; when a batch supplies
> the connection, the batch owns the policy. Without that branch the `TypeError` was raised for
> every message of every mass send the moment shared-connection batching landed — and it was
> swallowed by the surrounding `except`, so each send merely "returned False" and the operator got a
> tally of failures with no explanation anywhere.
>
> **`open_connection()` fails LOUDLY on purpose.** `with connection:` calls `open()` with
> `fail_silently=False`, so a TCP/TLS/AUTH failure propagates — in a request path that is a 500
> where the per-message loop would have reported "N no pudieron enviarse". Any batch must wrap the
> open in its own try/except; `core.views.app_forms._mass_send` does it for every mass mail in the
> app, and `comms.tasks._send_fun_friday_batch` does it for the drain task (whose row is already
> **claimed** by the time it sends, so an unwrapped failure would lose the announcement outright).

`LOGO_PATH` / `_get_logo_path()` were **removed** in v1.27.1 — nothing referenced either, and
`core/static/images/logo.png` was never attached to a message. An email image has to be a `cid:`
inline part (`inline_images=`), so a path helper nobody passes to that argument could not render
anything; it was deleted rather than kept "just in case", because it read like the logo was already
being embedded.

Templates live in `core/templates/emails/` and extend `emails/base_email.html`. There are currently **19 content templates** (20 files including the shared base): `happy_birthday`, `welcome_student`, `enrollment_child`, `enrollment_adult`, `fun_friday`, `payment_reminder`, `payment_reminder_simple`, `payment_receipt`, `receipt_quarterly_child`, `receipt_adult`, `receipt_enrollment`, `vacation_closure`, `tax_certificate`, `monthly_report`, `admin_monthly_report`, `newsletter`, `parent_temporary_password`, `teacher_activation`, `password_reset`, plus the shared `base_email`.

**Every content template must define its own `{% block title %}`** (v1.20.0). `base_email.html` supplies a generic "Five a Day" fallback and 11 of the 18 were silently taking it, so the document title bore no relation to the subject line. Give a new template a title matching its `subject=`, in the shared `"<asunto> · Five a Day"` shape.

### SmsService (`comms/services/sms_service.py`)

Twilio SMS sender (v1.8). The `twilio` client is imported lazily so the app runs fine without it.

- `is_configured()` — true only when all three of `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` are set
- `send(to, body)` — returns an `SmsResult`; the destination number and any Twilio error are passed through `safe_log()` in the log record *and* in `SmsResult.error`, because callers surface that text in responses
- `send_to_parent(parent, body)` — **strictly opt-in**: only parents with `sms_opt_in=True` receive anything
- `get_sms_service()` — singleton accessor

### Email Functions (`comms/services/email_functions.py`)

Convenience functions for each email type. Each wraps `email_service.send_email()` with template-specific parameters. **Six of them now take `connection=None`** (v1.27.1) so a batch can hand in one shared SMTP session: `send_monthly_report`, `send_fun_friday_email`, `send_payment_reminder_email`, `send_quarterly_receipt_email`, `send_vacation_closure_email`, `send_tax_certificate_email`.

| Function | Template | Trigger |
| -------- | -------- | ------- |
| `send_welcome_email` | `welcome_student` | On student creation |
| `send_enrollment_confirmation_email` | `enrollment_child` / `enrollment_adult` | On enrollment |
| `send_fun_friday_email` | `fun_friday` | Weekly manual |
| `send_payment_reminder_email` | `payment_reminder` | Monthly manual — takes five fee figures (`full_time_fee`, `part_time_fee`, `adult_fee`, `quarterly_fee`, `sibling_full_time_fee`). Any left `None` are filled from `PricingService.payment_reminder_fees()`, so no caller can send the tariff table blank (v1.20.0). `reduced_price_cheque_idioma` carries **no unit** — the template prints `{{ ... }} euros` (see `cheque_idioma_fee`) |
| `send_quarterly_receipt_email` | `receipt_quarterly_child` | Quarterly manual |
| `send_vacation_closure_email` | `vacation_closure` | Manual |
| `send_tax_certificate_email` | `tax_certificate` | Yearly (April) |
| `send_all_tax_certificates` | (iterates parents) | Yearly batch |
| `send_monthly_report` | `monthly_report` | Monthly manual |
| `generate_tax_certificate_pdf` | (HTML to PDF) | Called by tax certificate |

Plus one non-sending helper:

- **`cheque_idioma_fee(config=None) -> str`** (v1.27.1) — the monthly fee with Cheque Idioma applied, formatted **exactly like the other five rows** of the reminder tariff table. `emails/payment_reminder.html` prints `{{ reduced_price_cheque_idioma }} euros`, so a value carrying its own `€` rendered **"34€ euros"** — and all four emitters (the GET view, the preview, the POST and the `send_email` command) appended the symbol independently, so production has sent the double unit since v1.0. It derives from `SiteConfiguration` (never a fixed 34) and reuses `PricingService._euros`, because two different formatters in one table is precisely the bug above. `core.views.app_forms._cheque_idioma_text` is the request-side wrapper: it strips a trailing `€` an operator typed into the form and falls back to this helper.

`send_all_tax_certificates` is the one batch that **deliberately does not follow the mass-mail
rules** — two exceptions, both because the document attests money actually **paid**:

- **Waiting-list families are not excluded.** A family whose child moved onto the waiting list paid
  real fees and is entitled to their certificate. The query is driven by *completed* `Payment` rows,
  not by the student's state.
- **Addresses are not deduplicated.** Two `Parent` rows sharing a mailbox have their own payments
  and their own DNI, so they are two distinct fiscal documents; collapsing them would leave one of
  the two without a certificate.

It does share the batching: one SMTP session for the whole run (previously a handshake *and* a PDF
render per family), a failed `open()` reported as "all pending certificates failed" rather than
raised at the calling view, and a per-parent exception that never aborts the batch. Parents with no
address are counted as `skipped`.

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
| `send_welcome_email_task` | Async welcome email (includes the group's timetable). Reports the payment modality in Spanish, and "Especial" for a `special` matrícula — its cadence is whatever was agreed with the family, not a standard one (v1.20.0); v1.27.1 reads that from the shared `Enrollment.is_hand_priced` instead of a sixth inline copy of `enrollment_type.name == "special"`, and passes `fail_silently=True` like every other task in the file — without it an SMTP error escaped as the raw backend exception instead of the `RuntimeError` that `autoretry_for` and the student-creation caller are written against | On student creation, fired `on_commit` |
| `send_birthday_email_task` | Individual birthday email — every parent with an address, adult students themselves. v1.26.8 attaches `happy-birthday.png` as a **`cid:` inline part**; the template's `<img src="cid:birthday_image">` had nothing attached, so every birthday email ever sent rendered a broken image | Called by batch task. Production runs `CELERY_TASK_ALWAYS_EAGER=True`, so this executes **inline**: 2 queries per student since v1.26.1, down from 3 (a `prefetch_related` that its own `exclude()` discarded). Fan-out task costs are invisible in dev, where subtask queries land in the worker |
| `send_birthday_emails_task` | Daily birthday batch — goes to **all** of a student's parents, dated with `localdate()`. v1.27.1 adds `is_waiting=False`, matching every other mass mail: a student moved back onto the waiting list keeps their parents, and the manual `birthday_form` makes the same call, so the cron and the button must not disagree about who gets a card | Celery Beat (daily 08:00) / `send_birthday_emails` command |
| `send_payment_reminders` | Weekly payment reminder batch, deduped against the SMS channel. v1.27.1 reads `timezone.localdate()` **once**: it called `date.today()` twice, so a run straddling midnight built a window whose lower bound was the day *after* its upper bound's reference and silently reminded nobody | Celery Beat (Mondays 09:00) / `send_payment_reminders` command |
| `send_payment_reminder_sms_task` | Twilio SMS reminder for one payment — opt-in parents only (v1.8) | Called from the reminder batch |
| `send_monthly_report_task` | Admin monthly report. With no explicit recipient it now (v1.26.8) goes to **both** `SUPPORT_EMAIL` and `DEFAULT_FROM_EMAIL`, deduped — the academy reads it in two inboxes — and is skipped only when neither is set. `--recipient` still overrides both. v1.27.1 uses `timezone.localdate()` instead of `date.today()`: the container runs UTC, so a scheduled run at 00:xx or a late-evening run in CEST read the **wrong month** and the "informe mensual" then aggregated a month nobody asked for | Celery Beat (28th, 20:00) / `send_monthly_report` command |
| `send_parent_temporary_password_task` | Generates, hashes onto `Parent.temporary_password` and emails a one-off portal password — the invitation when the record is created, and the recovery from `¿Has olvidado tu contraseña?` (`reset=True` swaps the copy). The plaintext is generated **inside** the task, never passed as an argument: task arguments are serialised into the broker and printed in task logs (v1.9, reworked v1.27) | `ParentCreateView`, parent-portal recovery form |
| `send_payment_receipt_email_task` | Emails a receipt PDF for a completed payment (v1.11) | Payment completion / Stripe webhook |
| `send_generic_email_task` | Generic email dispatcher | Manual |
| `send_enrollment_confirmation_task` | Enrollment confirmation with attachments (uses `student.gender` field). v1.27.1 reads `core.constants.MESES_ES` instead of its own private `MONTHS_ES` copy — two lists of Spanish month names under different names is how two spellings of a month end up in the same product | On enrollment |
| `send_due_fun_friday_emails_task` | Drains due `FunFridayScheduledSend` rows (idempotent — rows are marked `sent_at`). **The only Fun Friday send path** — manual sends persist a row (drained immediately if its slot has passed) so the claim guard always applies. | Celery Beat (daily 14:30) / `send_due_fun_friday_emails` command |

`_send_fun_friday_batch()` is the shared loop behind both Fun Friday paths and, since v1.27.1, uses
**one SMTP session** for the whole announcement — this is the academy's largest single batch (every
family) and it was paying a handshake per address. The open is wrapped, because `open()` is not
`fail_silently` and the drain task's row is already **claimed** by the time it sends: an unwrapped
failure would lose the announcement outright. A per-recipient exception never aborts the batch.

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
python manage.py send_email --tax-certificate --year 2024 --recipient ana@example.com
```

**v1.27.1** brought every batch in this command onto the shared recipient rules
(`_mass_mail_parent_filters` / `_dedupe_emails` / `_mass_mail_recipients` — local twins of
`core.views.app_forms`, duplicated because a `comms` command importing `core.views` would invert the
layering, and each docstring names its counterpart):

- Waiting-list families are excluded, and `--monthly-report` no longer mails **every `Parent` row
  with an email**, including families that have left — each of whom received a report listing zero
  students.
- Addresses are deduplicated, so a couple sharing a mailbox gets one copy.
- The birthday batch filters `is_waiting=False`, like the task.
- `--cheque-idioma-price` goes through `cheque_idioma_fee`: the default used to be formatted with an
  appended `€` and the template prints `"… euros"`, so it rendered "34€ euros"; a `€` typed on the
  command line is now stripped rather than doubled.
- `--recipient` on `--tax-certificate` resolves with `email__iexact` + `[:2]` and **refuses an
  ambiguous match** — the same call `core.views.auth._parent_by_email` makes, for the same reason:
  picking whichever row came back first would mail one family's fiscal certificate, carrying the
  *other* family's payments and DNI, to the shared address. The dead `except Parent.DoesNotExist`
  around it (nothing in the block could raise it once the `.get()` was gone) was removed, because it
  read like a live path.

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
| `test_mass_mail_fixes.py` *(integration)* | The v1.27.1 mass-mail rework, each test named after the wrong behaviour it pins shut: waiting-list families receiving every mass mail, the Fun Friday counter/recipients/banner being three different numbers, no address deduplication, an SMTP `open()` 500ing the request instead of reporting a failure, a Fun Friday announcement lost to a `DataError` on a seconds-precision time (and a double submit scheduling it twice), "34€ euros", the PDF tables drawn past the right edge of the page, and the fiscal certificate merging same-named siblings into one subtotal |

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
