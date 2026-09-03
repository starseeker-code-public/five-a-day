# billing — Payments, Enrollments, Pricing

The `billing` app owns all financial logic: pricing configuration, enrollment plans, payment tracking, and data exports. It contains the service layer where core business logic lives.

## Models

| Model | Table | Key Fields |
| ----- | ----- | ---------- |
| **SiteConfiguration** | `site_configuration` | Singleton (pk=1). All pricing: enrollment fees, monthly fees, discount percentages/amounts |
| **EnrollmentType** | `enrollment_types` | The matrícula category: name (new_student, returning_student, adults, special), display_name, and `base_amount_*` = the one-time matrícula fee. Payment cadence lives on `Enrollment.payment_modality`, not here. |
| **Enrollment** | `enrollments` | FK to Student + EnrollmentType. schedule_type, payment_modality, discounts, amounts, status, academic_year. Indexed on `academic_year` for payment generation queries. |
| **Payment** | `payments` | FK to Student + Parent + Enrollment. amount, type, method, status, due_date, payment_date, stripe_session_id / stripe_payment_intent (v1.11). **`parent` is nullable** — adult students have no guardian. There is no `active` field; soft-delete was never implemented, so never filter on `active=True`. Carries `unique_pending_periodic_payment_per_month` (v1.26.1) — a partial unique index on (student, payment_type, due year, due month) over `pending` periodic rows, plus a composite `(payment_status, due_date)` index for the app's dominant filter shape. |
| **Expense** | `expenses` | description, category, amount, expense_date, notes + recurrence: is_recurring, recurring_frequency (`monthly` / `weekly` / `yearly`), recurring_day (1-28), recurring_month, recurring_weekdays (CSV of ints 0-6, Monday=0), generated_from (self-FK). v1.5 |

### Key Business Rules

- **SiteConfiguration** is a singleton — `get_config()` uses `get_or_create()` (race-condition safe), seeded from `billing/constants.py`. Always read pricing through it; `constants.py` values are seeds only. Includes `returning_student_enrollment_discount` (v1.13).
- **One active enrollment per student** — enforced by UniqueConstraint on `(student)` where `status='active'`
- **Payment.is_overdue** — True when status is pending and due_date < today
- **Enrollment.payment_totals()** — `(overdue, outstanding, billed)` in a single query, and the source for `is_up_to_date` / `overdue_amount` / `outstanding_amount`. It replaced `is_paid` / `remaining_amount`, which summed **every** completed payment on the enrollment — matrícula and each month's cuota together — and compared the total to `final_amount`, the price of **one period**. Different units on either side of the comparison: a monthly student owing 520 EUR over ten periods reported `is_paid=True` and `remaining_amount=0.00` as soon as a single 54 EUR month was collected, and a 40 EUR matrícula on its own left "remaining 14.00". `is_up_to_date` is driven by **overdue** money (past its due date, still pending) because that is the chase list; merely outstanding money is reported separately, since payments are created on the first day of a period and only fall due on its last, so counting them as owed would flag nearly every family for most of every month. Only `pending` counts, so cancelled / failed / refunded money is excluded
- **Expense.clean()** validates per cadence — `monthly` needs `recurring_day`; `yearly` needs `recurring_day` + `recurring_month`; `weekly` needs `recurring_weekdays`. Materialisation is idempotent on `generated_from` + the exact `expense_date`, and since v1.26.1 that is enforced by `unique_materialized_expense_per_date` rather than by an `.exists()` check alone — the monthly and daily materialisers run on different cadences over overlapping templates. A NULL `generated_from` never collides in Postgres, so hand-entered expenses are unaffected.

### Helper Functions (in models.py)

- `current_academic_year(date)` — returns "YYYY-YYYY" format (year starts in September)
- `academic_year_start_date(year)` — first Monday on/after September 14th
- `academic_year_end_date(year)` — last Friday in June

## Service Layer

### EnrollmentService (`billing/services/enrollment_service.py`)

- `create_enrollment(student, enrollment_data, is_adult)` — creates an Enrollment within `transaction.atomic()` with proper pricing and discounts. Raises `ValueError` if required EnrollmentType is missing. v1.26.8: `enrollment_data["start_date"]` (blank ⇒ today) sets `enrollment_date` **and** selects the academic year via `current_academic_year(start_date)`, so an enrollment created today for a 1 November start belongs to — and is billed from — November; `enrollment_data["is_returning_student"]` is the form's "Antiguo alumno" override.
- `_resolve_enrollment_type(student, is_adult, is_special, manual_amount, academic_year, force_returning=False)` — picks the matrícula category by precedence: hand-priced → `special`, adult → `adults`, has an earlier academic year → `returning_student`, otherwise `new_student`. Independent of `enrollment_plan`. `force_returning` (v1.26.8) marks a child as re-enrolling when no prior `Enrollment` row exists; it does **not** outrank `special` or `adults`.
- `_resolve_plan(config, data, is_adult, is_special, manual_amount)` — returns `(base_amount, schedule_type, payment_modality)`, i.e. the recurring period fee and how it is scheduled.
- `_apply_discounts(config, base, ...)` — applies sibling and language cheque discounts
- `compute_enrollment_fee(config, student, is_adult, special_fee=None, force_returning=False, this_academic_year=None)` — returns `(final_fee, returning_discount_applied)`. **`this_academic_year` must be passed by any caller that has just created the enrollment** (`enrollment.academic_year`): judged against today's year instead, a future-dated enrollment reads as the student's own prior history and wrongly wins the discount. `force_returning` is the "Antiguo alumno" checkbox — it grants the discount, never revokes one the prior enrollments already earn. `special_fee` (v1.20.0) is the form's optional **Matrícula especial (€)**: a negotiated figure, so it is returned verbatim with no returning-student discount taken off it. It is deliberately separate from `manual_amount`, which prices the *recurring* fee only — a special monthly price does not imply a special matrícula, and before v1.20.0 such an enrollment was silently charged the standard one.
- `is_returning_student(student)` / returning-student enrollment discount (v1.13) — a student who previously had an enrollment pays a reduced enrollment fee

### EnrollmentTypeService (`billing/services/enrollment_type_service.py`)

- `ensure_enrollment_types(config=None)` — idempotently creates the four `EnrollmentType` rows `_resolve_enrollment_type` can request (`new_student`, `returning_student`, `adults`, `special`) with Spanish `display_name`s and the matrícula fees from `SiteConfiguration` (`children_enrollment_fee`, that minus `returning_student_enrollment_discount`, `adult_enrollment_fee`, and the minimum for hand-priced `special`). Repairs drifted labels/amounts; never touches admin-edited `description` / `active`. Shared by the `seed_enrollment_types` command and `seed_testdata`.
- `REQUIRED_ENROLLMENT_TYPES` — the four categories (`new_student`, `returning_student`, `adults`, `special`). This is the complete set: every enrollment is exactly one of them.

### PaymentService (`billing/services/payment_service.py`)

- `_get_base_monthly_fee(enrollment, config)` — shared helper that resolves base fee by schedule type (adult_group / full_time / part_time)
- `hand_priced_amount(enrollment)` (v1.20.0) — the agreed **period** fee of a `special` matrícula, or `None`. A hand-priced enrollment stores the admin's amount on the enrollment itself (`final_amount`, already carrying whatever sibling / cheque discount was ticked), and its `schedule_type` is only the timetable the student attends, **not** a price band. Both generators used to re-derive the fee from `SiteConfiguration`, so the ficha showed the custom price while every payment of the year charged the standard 1-day / 2-day rate. Any new amount calculation must go through this helper, and any queryset feeding it needs `select_related("enrollment_type")`.
- `calculate_monthly_amount(enrollment, config, month)` — monthly payment with discounts + June bonus (delegates to `_get_base_monthly_fee`). Short-circuits on `hand_priced_amount()`: sibling / cheque / June discounts are **not** layered on top of a negotiated price, because `EnrollmentService._apply_discounts` already folded them in at creation.
- `calculate_quarterly_amount(enrollment, config, quarter_due_month)` — 3 months minus the quarterly discount, **then** the sibling percentage, the language cheque (x3, one per covered month) and — for Q3, which covers June — the June discount. Mirrors `EnrollmentService._apply_discounts` so the enrollment row and the generated payments agree. Before v1.15 only the quarterly percentage was applied, so a quarterly student with a sibling discount or a cheque was billed full price. Adult groups keep their flat rate. A `special` matrícula short-circuits all of it via `hand_priced_amount()` — for a quarterly special the admin types the price of the whole quarter.
- `schedule_academic_year_payments(enrollment, parent=None, as_of=None, billed_months=None)` — creates every periodic payment whose period has **started**, plus the first one always. Payments are created on the first day of their period and fall due on its **last** day, so enrolling issues just the period joined and the 1st-of-the-month cron opens each later one. Back-fills any started period that has no payment, so a missed cron run is repaired instead of going unbilled. Idempotent (matches on payment_type + due-date month/year). Called by `StudentCreateView`, waiting-list assignment and `generate_payments`. Returns the count created. Each create is wrapped in `transaction.atomic()` and an `IntegrityError` is swallowed — `unique_pending_periodic_payment_per_month` is what actually guarantees idempotency, and losing that race means the payment now exists, which is the outcome wanted. A passed `billed_months` set is **mutated** as rows are created, so a second call for the same student in one run stays idempotent.
- `billing_periods(enrollment)` — the schedule itself: ordered periods from the enrollment month to June, each `{months, starts, due, fraction}`. Monthly = one per teaching month; quarterly = consecutive 3-month blocks **anchored to the enrollment month** (12 Dec → Dec-Feb, Mar-May, one-month June stub), replacing the fixed Oct/Jan/Apr calendar that left a mid-year joiner's first months — and September for everyone — unbilled.
- `proration_fraction(reference, month, year)` — days left in the joining month, counting the join day (15 Sep on 30 days = 16/30). Applies to the first period only.
- `calculate_period_amount(enrollment, config, months, fraction=1, quarterly=False)` — the single source of what a period costs; every generator goes through it. `calculate_monthly_amount` / `calculate_quarterly_amount` are thin wrappers that answer the standard-price question *for an existing enrollment*. The two callers that ask it without one — the payment-reminder email and the pricing preview — cannot use them and re-derive the figures in `PricingService`; `tests/unit/test_pricing_matches_billing.py` asserts the two agree.
- `pending_periods(enrollment, as_of=None, billed_months=None)` — the periods that are due to be created right now, and the single decision point for "should this be billed yet?". Applies the two rules once: the first period is always offered (even when it opens in the future), and a period already carrying a payment of that type due in the same month is skipped — cancelled rows included, so a soft-deleted payment is not re-created. `schedule_academic_year_payments` and `generate_payments --dry-run` both go through it; they used to apply the rules separately and had already drifted on both. Costs one query per enrollment — pass `billed_months` from `billed_months_map()` to skip even that. `None` means "resolve it yourself"; an **empty set** means "this student has nothing billed", so the check is `is None`, not falsiness.
- `billed_months_map(student_ids)` — `{(student_id, payment_type): {(month, year), …}}` for a whole roster in **one** query (v1.26.1). `pending_periods` is called in a loop over every active enrollment by both `generate_payments` and `reconcile_payment_schedule`, at exactly one `SELECT payments` each — ~2,000 round trips per monthly run at the academy's ceiling. Both commands now materialise their queryset, build this once and pass slices in: the dry run went 64 → 4 queries, the idempotent re-run 63 → 3, reconcile 243 → 6. Keyed on `(student_id, payment_type)` because that is what the per-row query matched on, and every status is included so a cancelled payment still occupies its month.
- `period_concept(period, quarterly)` — the label, e.g. `Trimestre Diciembre-Febrero 2026 (parcial)`.

### PricingService (`billing/services/pricing_service.py`)

- `get_config()` — cached SiteConfiguration access
- `get_monthly_fee(schedule_type)` — fee by full_time/part_time/adult_group
- `get_enrollment_fee(is_adult)` — child vs adult enrollment fee
- `calculate_quarterly_price()` — 3 months * full_time - discount%
- `calculate_sibling_price(config=None, schedule_type="full_time")` (v1.20.0) — the monthly fee with the sibling discount applied, in the same order of operations as `PaymentService.calculate_period_amount`, so the figure advertised in the payment-reminder email matches what the sibling is actually billed. A deliberate re-derivation, not a call — the billing helpers need an `Enrollment` and this question has none; `tests/unit/test_pricing_matches_billing.py` keeps the two in step
- `payment_reminder_fees(config=None)` (v1.20.0) — the display-ready five-row fee table for the `payment_reminder` email (full-time, part-time, adult, quarterly, sibling full-time), formatted the way the Spanish emails print money (comma decimal, no `,00` tail). The quarterly and sibling rows used to read *"consultar en la academia"*; every caller that renders the template now shares one source of truth

### ExpenseService (`billing/services/expense_service.py`)

- `monthly_totals(month, year)` — per-category expense aggregation for the expenses page and the reports dashboard
- `materialize_recurring(month, year)` — spawns real expense rows from **monthly** recurring templates (Beat: 1st of the month, 06:30)
- `materialize_recurring_for_date(target_date)` — spawns rows from **weekly** (`recurring_weekdays`) and **yearly** templates (Beat: daily 06:15)
- Both are idempotent — they match on `generated_from` + the exact `expense_date`, so a re-run never double-creates

### GcpCostService (`billing/services/gcp_cost_service.py`)

Real Google Cloud spend, month by month — GCP only exposes actual costs through the **BigQuery
billing export**, so this queries that table over BigQuery's REST API with the google-auth stack
gspread already pulls in (no new dependency). Gated on `GCP_BILLING_EXPORT_TABLE`; unconfigured or
unreachable means `None` everywhere (the UI renders "—") and never an exception.

- `month_cost(year, month)` — live net cost (cost + credits) for an invoice month, cached in the
  Django cache (6 h for the running month, 24 h for closed ones, 10 min for failures so a broken
  export cannot add a BigQuery timeout to every page render)
- `archive_month(year, month)` — persists a **finished** month as a real `category="software"`
  Expense row dated the month's last day. Idempotent (matches on the fixed description
  `"Google Cloud Platform"`); a `cache.add()` slot guards the read-then-create against overlapping
  runs. Skips months under 0.01 € (`Expense.amount` has `MinValueValidator(0.01)`)
- `archived_gcp_expense(year, month)` / `previous_month(today)` — lookup helpers
- `qa_card_amounts(today)` — the `/testing/` "Gastos GCP" line: previous month (archived row
  preferred, live fallback) + current month (always live)

The design split: the **running** month is dynamic (read live, never persisted — the expenses page
folds it into the displayed totals as a read-only "(mes en curso)" row), a **finished** month is a
saved value (the archived row is the source of truth for every calculation from then on).

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

## Forms (billing/forms.py)

- **EnrollmentForm** — the enrollment half of the student-creation page. Plain `forms.Form` (not a
  ModelForm): it validates, and `create_enrollment(student, is_adult)` is a thin bridge to
  `EnrollmentService.create_enrollment`.
  - `enrollment_plan`, `has_language_cheque`, `is_sibling_discount`, `sibling_id`, `is_special`
  - `start_date` (v1.26.8) — **Fecha de inicio**, the day the student actually STARTS, which need
    not be the day the ficha is created. Blank means today (`initial` is the `date.today` callable,
    so it is evaluated per render, not at import). It becomes `Enrollment.enrollment_date`, and with
    it the academic year, the matrícula's due month, the first billing period and its proration.
  - `is_returning_student` (v1.26.8) — **Antiguo alumno**. Forces the returning-student matrícula
    for a `Student` row with no prior `Enrollment` (someone re-registering after years away, or
    promoted off the waiting list, which creates a fresh row). It only ever adds the discount.
  - `manual_amount` — **Precio manual (€)**, the *recurring* fee (per month, or per quarter on a
    quarterly plan). Required when `is_special` is ticked.
  - `special_enrollment_fee` (v1.20.0) — **Matrícula especial (€)**, the optional *one-time*
    matrícula. Left blank the standard matrícula applies; a special cuota does **not** imply a
    special matrícula. `clean()` rejects it unless "Precio especial" is ticked, because silently
    ignoring it would charge the standard fee while the admin believes they set one. It is not
    stored on `Enrollment` — the `payment_type="enrollment"` Payment row is the record.

## Constants (billing/constants.py)

- Pricing seed values (used in SiteConfiguration defaults)
- Choice tuples: ENROLLMENT_TYPE_CHOICES, SCHEDULE_TYPE_CHOICES, PAYMENT_MODALITY_CHOICES, etc.
- **Choice labels are Spanish** (v1.20.0) — `get_<field>_display()` output goes straight onto the payment detail page, the payments list, the student ficha and the admin, and `PAYMENT_TYPE_CHOICES` / `PAYMENT_STATUS_CHOICES` / `ENROLLMENT_STATUS_CHOICES` shipped English labels in the middle of a Spanish UI. The *keys* stay English, so no data migration is involved — but Django still generates an `AlterField` (`0009`), which must be committed
- `LIVE_PAYMENT_STATUSES` (`pending`, `completed`) — money the academy still expects or already has; the single filter for "esperado". `PERIODIC_PAYMENT_TYPES` (`monthly`, `quarterly`) — the types the recurring schedule issues, as opposed to the one-off matrícula and ad-hoc rows. Moved here in v1.26.1 from a private copy in `reconcile_payment_schedule`; `billed_months_map` and the `unique_pending_periodic_payment_per_month` constraint both key off it (the constraint repeats the tuple as a literal, because a migration can only compare literals — keep the two in step)
- Utility functions: `calculate_discount()`, `get_enrollment_fee()` — `get_monthly_fee_by_schedule()` was removed in v1.26.0 (referenced only by its own tests; `PricingService.get_monthly_fee` is the live equivalent)

## Management Commands

### `seed_enrollment_types`

```bash
python manage.py seed_enrollment_types
```

Provisions the `EnrollmentType` reference table. **Required in every environment** — nothing else creates these rows (`0001_initial` builds the table and inserts nothing), and without them `EnrollmentService._resolve_enrollment_type` raises `EnrollmentType '<name>' not found` and no student can be enrolled. `entrypoint.sh` runs it on every testing/production boot beside `seed_teachers`. Idempotent. Since v1.26.8 `entrypoint.sh` runs it in **every** environment, not just testing/production.

### `generate_payments`

```bash
python manage.py generate_payments              # Current month
python manage.py generate_payments --month 10 --year 2025
python manage.py generate_payments --dry-run    # Preview only
```

Opens every billing period that has started for each active enrollment — the new month for monthly students, the new block for quarterly ones. Runs on the 1st of the month. It calls the same `PaymentService.schedule_academic_year_payments()` the enrollment form does, so there is a single code path, and it **back-fills**: a period that started without a payment is created, so a missed run is repaired on the next one rather than going permanently unbilled. Skips anything already billed for that period.

### `reconcile_payment_schedule`

```bash
python manage.py reconcile_payment_schedule                          # DRY RUN — report only
python manage.py reconcile_payment_schedule --apply                  # fill gaps
python manage.py reconcile_payment_schedule --apply --cancel-stale   # + retire superseded rows
python manage.py reconcile_payment_schedule --academic-year 2025-2026
```

One-off migration aid for v1.22.0, which anchored quarters to the enrollment month. Payments written under the old fixed Oct/Jan/Apr calendar do **not** repair themselves — the idempotency check matches on due month/year and the new due dates differ, so a plain `generate_payments` re-run would create a second, overlapping set. This command reconciles instead: it creates periods the new schedule wants but that have no payment, cancels (never deletes) **pending** rows matching no period, and refuses to touch any enrollment with a **completed** payment, reporting it as `REVIEW` for a human instead — rewriting a settled schedule corrupts the books. Dry run unless `--apply`; idempotent, so re-running reports `0 payment(s) to create`. Since v1.26.1 it **cancels before it creates** — a stale pending row still occupies its month's slot under `unique_pending_periodic_payment_per_month`, and the v1.22.0 re-anchoring moves due dates *within* overlapping months, so creating first made the repair collide with the very row it was superseding. It also reads every enrollment's payments in one query rather than one per row, and the per-enrollment transaction is taken only when `--apply` is given (the dry run keeps its rollback net as one outer transaction instead of a savepoint per enrollment). See DEPLOYMENT.md for the testing and production runbooks.

### `materialize_recurring_expenses`

```bash
python manage.py materialize_recurring_expenses                    # Monthly templates (1st-of-month job)
python manage.py materialize_recurring_expenses --month 3 --year 2027
python manage.py materialize_recurring_expenses --daily            # Weekly + yearly templates (daily job)
python manage.py materialize_recurring_expenses --daily --date 2027-03-15
```

Wraps the two recurring-expense Celery tasks (`materialize_recurring_expenses_task` / `_daily_task`) so external schedulers (Cloud Scheduler → Cloud Run Jobs in production) can run them without Celery Beat. Both paths are idempotent.

### `archive_gcp_costs`

```bash
python manage.py archive_gcp_costs                     # The month before today
python manage.py archive_gcp_costs --month 8 --year 2026   # Backfill a specific month
```

Wraps `archive_gcp_costs_task`: stores a finished month's real Google Cloud spend as a concrete
`software` Expense row (see `GcpCostService`). Idempotent — an already-archived month is reported
and skipped; exits non-zero when BigQuery is unreachable so Cloud Run Jobs retry the run, and 0
with a warning when the feature is unconfigured.

## Celery Tasks (billing/tasks.py)

| Task | Beat schedule | Command wrapper |
| ---- | ------------- | --------------- |
| `generate_monthly_payments_task` | 1st of month, 06:00 | `manage.py generate_payments` |
| `materialize_recurring_expenses_task` | 1st of month, 06:30 | `manage.py materialize_recurring_expenses` |
| `materialize_recurring_expenses_daily_task` | daily, 06:15 | `manage.py materialize_recurring_expenses --daily` |
| `archive_gcp_costs_task` | 3rd of month, 06:45 | `manage.py archive_gcp_costs` |

Production runs no Beat process (Cloud Run, `CELERY_TASK_ALWAYS_EAGER=True`) — Cloud Scheduler
triggers Cloud Run Jobs that call the management-command wrappers instead. Never schedule with
`apply_async(eta=...)` or `countdown`: under eager mode the delay is ignored and the task runs
immediately.

## Admin (`billing/admin.py`)

`*/admin.py` is excluded from coverage; these are covered by
`tests/integration/test_admin_hardening.py`.

| ModelAdmin | Behaviour |
| ---------- | --------- |
| `PaymentAdmin` | Student/parent links (both `None`-safe — an adult student has no guardian), amount and status columns, CSV export through `csv_safe_row`. **Bulk actions (v1.26.0):** `mark_as_completed` now touches only *pending* rows and queues a receipt for each. It was a bare `queryset.update(payment_date=today)`, which rewrote the date on already-completed payments — moving settled money into the current month in every income report, the regression `quick_complete_payment` short-circuits on — and sent nothing. Reopening a payment clears `payment_date`, because every income figure filters on it |
| `EnrollmentAdmin` | Four-state `payment_status_display` (v1.26.0): overdue / up-to-date-with-an-open-period / settled / not-yet-billed. It was a paid-unpaid pair driven by `is_paid`, which compared a year of collected money against one period's price. The **Billing plan** fieldset (`academic_year`, `payment_modality`, `is_sibling_discount`, `has_language_cheque`) was added at the same time — none of those fields were on any form, and `academic_year` is what `generate_payments` filters on, so a wrong value meant silently never billed with nowhere to fix it |
| `EnrollmentTypeAdmin` | Registered bare until v1.26.0. The four rows are resolved **by name** and a missing one raises `ValueError`, which blocks every enrollment of every kind — and a row not yet referenced by an enrollment had no `PROTECT` guarding it, so it was one click from deletion. Delete is refused for the four required names, `name` is read-only once the row exists (`choices` stops you inventing a value, not swapping `special` onto the row resolved as `adults`), and the matrícula amounts are visible in the list |
| `SiteConfigurationAdmin` | Singleton — add is refused once a row exists, delete always |
| `ExpenseAdmin` | List/filter/search; `generated_from` read-only |

## URL Patterns (billing/urls.py)

Payment CRUD, enrollment API, management panel, expenses (create / **update** (v1.20.0) / delete),
reports, search/statistics, Stripe endpoints, CSV/Excel export, per-student payment-history PDF
(v1.15). **24 URL patterns** total.

## Cross-App Communication

- **Depends on**: students (FK to Student, Parent in Enrollment and Payment models)
- **Depended on by**: core views (dashboard shows payment stats), comms (email functions reference Payment for tax certificates)
- **Exports used by core**: `SiteConfiguration`, `Enrollment`, `Payment`, `current_academic_year`, service classes
