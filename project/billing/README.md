# billing — Payments, Enrollments, Pricing

The `billing` app owns all financial logic: pricing configuration, enrollment plans, payment tracking, and data exports. It contains the service layer where core business logic lives.

## Models

| Model | Table | Key Fields |
| ----- | ----- | ---------- |
| **SiteConfiguration** | `site_configuration` | Singleton (pk=1). All pricing: enrollment fees, monthly fees, discount percentages/amounts |
| **EnrollmentType** | `enrollment_types` | name (monthly, quarterly, adults, special), display_name, base amounts |
| **Enrollment** | `enrollments` | FK to Student + EnrollmentType. schedule_type, payment_modality, discounts, amounts, status, academic_year. Indexed on `academic_year` for payment generation queries. |
| **Payment** | `payments` | FK to Student + Parent + Enrollment. amount, type, method, status, due_date, payment_date, stripe_session_id / stripe_payment_intent (v1.11). **`parent` is nullable** — adult students have no guardian. There is no `active` field; soft-delete was never implemented, so never filter on `active=True`. |
| **Expense** | `expenses` | description, category, amount, expense_date, notes + recurrence: is_recurring, recurring_frequency (`monthly` / `weekly` / `yearly`), recurring_day (1-28), recurring_month, recurring_weekdays (CSV of ints 0-6, Monday=0), generated_from (self-FK). v1.5 |

### Key Business Rules

- **SiteConfiguration** is a singleton — `get_config()` uses `get_or_create()` (race-condition safe), seeded from `billing/constants.py`. Always read pricing through it; `constants.py` values are seeds only. Includes `returning_student_enrollment_discount` (v1.13).
- **One active enrollment per student** — enforced by UniqueConstraint on `(student)` where `status='active'`
- **Payment.is_overdue** — True when status is pending and due_date < today
- **Enrollment.is_paid** / **remaining_amount** — calculated from completed payment totals via shared `_total_paid()` helper (single query path)
- **Expense.clean()** validates per cadence — `monthly` needs `recurring_day`; `yearly` needs `recurring_day` + `recurring_month`; `weekly` needs `recurring_weekdays`. Materialisation is idempotent on `generated_from` + the exact `expense_date`.

### Helper Functions (in models.py)

- `current_academic_year(date)` — returns "YYYY-YYYY" format (year starts in September)
- `academic_year_start_date(year)` — first Monday on/after September 14th
- `academic_year_end_date(year)` — last Friday in June

## Service Layer

### EnrollmentService (`billing/services/enrollment_service.py`)

- `create_enrollment(student, enrollment_data, is_adult)` — creates an Enrollment within `transaction.atomic()` with proper pricing and discounts. Raises `ValueError` if required EnrollmentType is missing.
- `_resolve_plan(config, data, ...)` — determines enrollment type, base amount, schedule type, payment modality
- `_apply_discounts(config, base, ...)` — applies sibling and language cheque discounts
- `is_returning_student(student)` / returning-student enrollment discount (v1.13) — a student who previously had an enrollment pays a reduced enrollment fee

### EnrollmentTypeService (`billing/services/enrollment_type_service.py`)

- `ensure_enrollment_types(config=None)` — idempotently creates the four `EnrollmentType` rows `_resolve_plan` can request (`monthly`, `quarterly`, `adults`, `special`) with Spanish `display_name`s and amounts from `SiteConfiguration`. Repairs drifted labels/amounts; never touches admin-edited `description` / `active`. Shared by the `seed_enrollment_types` command and `seed_testdata`.
- `REQUIRED_ENROLLMENT_TYPES` — the four names. `half_month` and `languages_ticket` are valid choices with Spanish labels but are modelled as discounts, never resolved to a row.

### PaymentService (`billing/services/payment_service.py`)

- `_get_base_monthly_fee(enrollment, config)` — shared helper that resolves base fee by schedule type (adult_group / full_time / part_time)
- `calculate_monthly_amount(enrollment, config, month)` — monthly payment with discounts + June bonus (delegates to `_get_base_monthly_fee`)
- `calculate_quarterly_amount(enrollment, config, quarter_due_month)` — 3 months minus the quarterly discount, **then** the sibling percentage, the language cheque (x3, one per covered month) and — for Q3, which covers June — the June discount. Mirrors `EnrollmentService._apply_discounts` so the enrollment row and the generated payments agree. Before v1.15 only the quarterly percentage was applied, so a quarterly student with a sibling discount or a cheque was billed full price. Adult groups keep their flat rate.
- `complete_payment(payment_id)` — marks payment completed with today's date (within `transaction.atomic()`)
- `schedule_academic_year_payments(enrollment, parent=None)` — on enrollment, creates all pending periodic payments for the academic year: monthly (Sep–Jun) or quarterly (Oct/Jan/Apr), each due at period end, starting at the enrollment month. Idempotent (matches on payment_type + due-date month/year) so the periodic `generate_payments` command never double-creates. Called by `StudentCreateView` and waiting-list assignment. Returns the count created.
- `should_generate_monthly/quarterly(month)` — academic calendar validation
- `get_payment_statistics(month, year)` — aggregate pending/completed counts and totals

### PricingService (`billing/services/pricing_service.py`)

- `get_config()` — cached SiteConfiguration access
- `get_monthly_fee(schedule_type)` — fee by full_time/part_time/adult_group
- `get_enrollment_fee(is_adult)` — child vs adult enrollment fee
- `calculate_quarterly_price()` — 3 months * full_time - discount%

### ExpenseService (`billing/services/expense_service.py`)

- `monthly_totals(month, year)` — per-category expense aggregation for the expenses page and the reports dashboard
- `materialize_recurring(month, year)` — spawns real expense rows from **monthly** recurring templates (Beat: 1st of the month, 06:30)
- `materialize_recurring_for_date(target_date)` — spawns rows from **weekly** (`recurring_weekdays`) and **yearly** templates (Beat: daily 06:15)
- Both are idempotent — they match on `generated_from` + the exact `expense_date`, so a re-run never double-creates

### PdfService (`billing/services/pdf_service.py`)

- `generate_payment_receipt(payment)` — single-payment receipt PDF (v1.3, reportlab)
- `generate_quarterly_summary(...)` — quarterly statement
- `generate_tax_certificate(...)` — annual certificate for a parent's tax return
- `generate_student_payment_history(student, payments, title_suffix="")` (v1.15) — a student's full payment history: concept, type, due date, **payment date**, **method** and status per row, with collected and outstanding totalled separately. Served by `student_payments_pdf`

All text reaching a reportlab `Paragraph` goes through `_md()`. Paragraph parses a mini-HTML dialect, so an unescaped name like `O<Brien` raised `paraparser: syntax error` and killed generation outright.
- Academy details come from the `ACADEMY_*` env vars, with a fallback when unset

### StripeService (`billing/services/stripe_service.py`)

Implemented directly against the Stripe REST API with `httpx` — **no Stripe SDK dependency** (v1.11).

- `is_configured()` — true only when `STRIPE_SECRET_KEY` is set; gates the parent-portal "Pagar online" button
- `create_checkout_session(payment)` — returns a hosted Checkout URL
- `verify_webhook_signature(payload, header)` — HMAC check against `STRIPE_WEBHOOK_SECRET`. **Required in production**: with the secret unset the webhook view skips verification entirely, so any HTTP client could mark payments as paid
- `apply_webhook_event(event)` — replay-safe; marks the payment completed and records `stripe_payment_intent`

## Constants (billing/constants.py)

- Pricing seed values (used in SiteConfiguration defaults)
- Choice tuples: ENROLLMENT_TYPE_CHOICES, SCHEDULE_TYPE_CHOICES, PAYMENT_MODALITY_CHOICES, etc.
- QUARTERS definition (Q1: Oct-Dec, Q2: Jan-Mar, Q3: Apr-Jun)
- Utility functions: `calculate_discount()`, `get_monthly_fee_by_schedule()`, `get_enrollment_fee()`

## Management Commands

### `seed_enrollment_types`

```bash
python manage.py seed_enrollment_types
```

Provisions the `EnrollmentType` reference table. **Required in every environment** — nothing else creates these rows (`0001_initial` builds the table and inserts nothing), and without them `EnrollmentService._resolve_plan` raises `EnrollmentType '<name>' not found` and no student can be enrolled. `entrypoint.sh` runs it on every testing/production boot beside `seed_teachers`. Idempotent.

### `generate_payments`

```bash
python manage.py generate_payments              # Current month
python manage.py generate_payments --month 10 --year 2025
python manage.py generate_payments --dry-run    # Preview only
```

Generates pending payments for all active enrollments. Monthly students get one per month (Sep-Jun). Quarterly students get one per quarter (Oct, Jan, Apr). Skips if payment already exists for that period.

### `materialize_recurring_expenses`

```bash
python manage.py materialize_recurring_expenses                    # Monthly templates (1st-of-month job)
python manage.py materialize_recurring_expenses --month 3 --year 2027
python manage.py materialize_recurring_expenses --daily            # Weekly + yearly templates (daily job)
python manage.py materialize_recurring_expenses --daily --date 2027-03-15
```

Wraps the two recurring-expense Celery tasks (`materialize_recurring_expenses_task` / `_daily_task`) so external schedulers (Cloud Scheduler → Cloud Run Jobs in production) can run them without Celery Beat. Both paths are idempotent.

## Celery Tasks (billing/tasks.py)

| Task | Beat schedule | Command wrapper |
| ---- | ------------- | --------------- |
| `generate_monthly_payments_task` | 1st of month, 06:00 | `manage.py generate_payments` |
| `materialize_recurring_expenses_task` | 1st of month, 06:30 | `manage.py materialize_recurring_expenses` |
| `materialize_recurring_expenses_daily_task` | daily, 06:15 | `manage.py materialize_recurring_expenses --daily` |

Production runs no Beat process (Cloud Run, `CELERY_TASK_ALWAYS_EAGER=True`) — Cloud Scheduler
triggers Cloud Run Jobs that call the management-command wrappers instead. Never schedule with
`apply_async(eta=...)` or `countdown`: under eager mode the delay is ignored and the task runs
immediately.

## URL Patterns (billing/urls.py)

Payment CRUD, enrollment API, management panel, expenses, reports, search/statistics, Stripe
endpoints, CSV/Excel export, per-student payment-history PDF (v1.15). **24 URL patterns** total.

## Cross-App Communication

- **Depends on**: students (FK to Student, Parent in Enrollment and Payment models)
- **Depended on by**: core views (dashboard shows payment stats), comms (email functions reference Payment for tax certificates)
- **Exports used by core**: `SiteConfiguration`, `Enrollment`, `Payment`, `current_academic_year`, service classes
