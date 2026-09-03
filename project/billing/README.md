# billing — Payments, Enrollments, Pricing

The `billing` app owns all financial logic: pricing configuration, enrollment plans, payment tracking, and data exports. It contains the service layer where core business logic lives.

## Models

| Model | Table | Key Fields |
| ----- | ----- | ---------- |
| **SiteConfiguration** | `site_configuration` | Singleton (pk=1). All pricing: enrollment fees, monthly fees, discount percentages/amounts. Plus the five `academy_*` fiscal fields (v1.27.1, migration `0013`): `academy_name`, `academy_cif`, `academy_address`, `academy_phone`, `academy_website` |
| **EnrollmentType** | `enrollment_types` | The matrícula category: name (new_student, returning_student, adults, special), display_name, and `base_amount_*` = the one-time matrícula fee. Payment cadence lives on `Enrollment.payment_modality`, not here. |
| **Enrollment** | `enrollments` | FK to Student + EnrollmentType. schedule_type, payment_modality, discounts, amounts, status, academic_year. Indexed on `academic_year` for payment generation queries. |
| **Payment** | `payments` | FK to Student + Parent + Enrollment. amount, type, method, status, due_date, payment_date, stripe_session_id / stripe_payment_intent (v1.11). **`parent` is nullable** — adult students have no guardian. There is no `active` field; soft-delete was never implemented, so never filter on `active=True`. Carries `unique_pending_periodic_payment_per_month` (v1.26.1) — a partial unique index on (student, payment_type, due year, due month) over `pending` periodic rows, plus a composite `(payment_status, due_date)` index for the app's dominant filter shape. |
| **Expense** | `expenses` | description, category, amount, expense_date, notes + recurrence: is_recurring, recurring_frequency (`monthly` / `weekly` / `yearly`), recurring_day (1-31; 29/30/31 clamp to the month's last day), recurring_month, recurring_weekdays (CSV of ints 0-6, Monday=0), generated_from (self-FK). v1.5 |

### Key Business Rules

- **SiteConfiguration** is a singleton — `get_config()` uses `get_or_create()` (race-condition safe), seeded from `billing/constants.py`. Always read pricing through it; `constants.py` values are seeds only. Includes `returning_student_enrollment_discount` (v1.13). Memoised per request in a `ContextVar` — and since v1.27.1 also **per Celery task**: `save()` only clears the memo in the process that ran the save, and a ContextVar is per-process state, so a worker started before an admin edited prices in the *web* process went on billing the old price for as long as it lived. `task_prerun` / `task_postrun` are the worker's equivalent of `request_started` / `request_finished`
- **`academy_*` fiscal fields** (v1.27.1) — `pdf_service._get_academy_info()` had always read these five names off the config with `getattr(..., "")`, and **none existed**, so every value fell through to the hard-coded `AcademyInfo` defaults and `academy_cif` fell through to blank. The tax certificate asserts IRPF deductibility while naming no CIF — the one field that makes it deductible — so the document was cosmetically fine and fiscally useless, with nowhere in the app to fix it. All five are `blank=True` because `update_site_config` runs `full_clean()` on **every price edit** and a non-blank CharField would 400 all of them until somebody filled these in; the defaults mirror `AcademyInfo` so a fresh install keeps producing today's document
- **One active enrollment per student** — enforced by UniqueConstraint on `(student)` where `status='active'`
- **Enrollment.is_hand_priced** (v1.27.1) — the single answer to "is this a `special` matrícula whose period price was typed by hand?". The predicate `enrollment_type.name == "special"` was inlined at five sites (two student views, the modality endpoint, the welcome-email task and `PaymentService.hand_priced_amount`), each deciding on its own whether a negotiated price may be re-derived from `SiteConfiguration`; one copy disagreeing is a family billed the standard rate against a price the academy agreed with them. Guarded on `enrollment_type_id`, not on the attribute — a non-nullable FK raises `RelatedObjectDoesNotExist` when it was never set, which is the shape of the bare carrier enrollments the pricing tests build
- **Payment.is_overdue** — True when status is pending and due_date < today
- **Payment.assert_completable() / Payment.DEAD_STATUSES** (v1.27.1) — a payment may only be completed from `pending`. `DEAD_STATUSES` is `("cancelled", "refunded")`: cancelling **frees the month** under `unique_pending_periodic_payment_per_month` (which is pending-only), so the schedule may already have re-billed it and nothing would stop a `completed` duplicate; a refunded payment is worse — the money went back to the family, so completing it re-books income that no longer exists and emails a receipt for a refund. It is a method rather than inline in `clean()` because the two callers need it at different moments: `clean()` runs it on every validating write path and re-reads the *stored* status (the instance's field is already overwritten by then), while `quick_complete_payment` runs it on the row **as loaded, before mutating it**, so it can answer 400 with the model's own Spanish wording. The endpoint's previous inline list named only `cancelled` — and `save()` never runs `clean()` — which is how it resurrected refunds
- **`UNCOLLECTED_PAYMENT_STATUSES`** (`billing/models.py`, v1.27.1) — `("pending",)`, money invoiced and not yet collected. Deliberately **not** `billing.constants.LIVE_PAYMENT_STATUSES` (`pending` + `completed`), which answers a different question: money the academy still *expects*. Folding `completed` in here would report already-banked money as outstanding debt and put every up-to-date family on the chase list. The constant exists so `Enrollment.payment_totals()` and `EnrollmentAdmin.get_queryset`'s annotations — one being an optimisation of the other — cannot drift apart
- **Enrollment.payment_totals()** — `(overdue, outstanding, billed)` in a single query, and the source for `is_up_to_date` / `overdue_amount` / `outstanding_amount`. It replaced `is_paid` / `remaining_amount`, which summed **every** completed payment on the enrollment — matrícula and each month's cuota together — and compared the total to `final_amount`, the price of **one period**. Different units on either side of the comparison: a monthly student owing 520 EUR over ten periods reported `is_paid=True` and `remaining_amount=0.00` as soon as a single 54 EUR month was collected, and a 40 EUR matrícula on its own left "remaining 14.00". `is_up_to_date` is driven by **overdue** money (past its due date, still pending) because that is the chase list; merely outstanding money is reported separately, since payments are created on the first day of a period and only fall due on its last, so counting them as owed would flag nearly every family for most of every month. Only `UNCOLLECTED_PAYMENT_STATUSES` counts, so cancelled / failed / refunded money is excluded — and so is `completed`, which already arrived
- **Expense.clean()** validates per cadence — `monthly` needs `recurring_day` (**1-31**); `yearly` needs `recurring_day` + `recurring_month`; `weekly` needs `recurring_weekdays`. Every message is Spanish as of v1.27.1: they reach the admin form and `create_expense` / `update_expense`'s JSON `message`, and the weekly branch was already Spanish while the others were not. **`recurring_day` is 1-31, not 1-28**, in all four places that state it (the class docstring, the field's `help_text`, `clean()` and `recurring_summary()`); the docstring and help_text said 1-28 while the validator accepted 1-31, which is the sort of disagreement that gets "fixed" in the tightening direction and quietly breaks the academy's rent. 29/30/31 are deliberately legal and mean **"the last day of the month"** — `expense_service` clamps to `min(recurring_day, last_day_of_month)` when materialising, and `recurring_summary()` renders anything from 29 up as "Mensual · último día del mes". A 1-28 bound would reject the value the expenses form itself offers (`max="31"`) and make every existing 29-31 template un-editable, since `update_expense` re-runs `full_clean()`. Materialisation is idempotent on `generated_from` + the exact `expense_date`, and since v1.26.1 that is enforced by `unique_materialized_expense_per_date` rather than by an `.exists()` check alone — the monthly and daily materialisers run on different cadences over overlapping templates. A NULL `generated_from` never collides in Postgres, so hand-entered expenses are unaffected.

### Helper Functions (in models.py)

- `current_academic_year(date)` — returns "YYYY-YYYY" format (year starts in September)
- `academic_year_start_date(year)` — first Monday on/after September 14th
- `academic_year_end_date(year)` — last Friday in June

### Migrations

The app is at **13 migrations**, through `0013_siteconfiguration_academy_fields` (v1.27.1 — the five
`academy_*` columns, plus Spanish `verbose_name` / `help_text` on the three `Expense` recurrence
fields; the latter are metadata only, but Django emits the `AlterField`s and they must be committed
or `makemigrations --check` fails CI). Two earlier ones are load-bearing: `0010` **refuses to apply**
against a database that already holds duplicate pending periodic payments (see
`unique_pending_periodic_payment_per_month`), and `0008` converts pre-v1.17.3 `EnrollmentType` rows
while no-opping on an empty table so `seed_enrollment_types` stays the single provisioning path.

## Service Layer

### EnrollmentService (`billing/services/enrollment_service.py`)

- `create_enrollment(student, enrollment_data, is_adult)` — creates an Enrollment within `transaction.atomic()` with proper pricing and discounts. Raises `ValueError` if required EnrollmentType is missing. v1.26.8: `enrollment_data["start_date"]` (blank ⇒ today) sets `enrollment_date` **and** selects the academic year via `current_academic_year(start_date)`, so an enrollment created today for a 1 November start belongs to — and is billed from — November; `enrollment_data["is_returning_student"]` is the form's "Antiguo alumno" override.
- `_resolve_enrollment_type(student, is_adult, is_special, manual_amount, academic_year, force_returning=False)` — picks the matrícula category by precedence: hand-priced → `special`, adult → `adults`, has an earlier academic year → `returning_student`, otherwise `new_student`. Independent of `enrollment_plan`. `force_returning` (v1.26.8) marks a child as re-enrolling when no prior `Enrollment` row exists; it does **not** outrank `special` or `adults`.
- `_resolve_plan(config, data, is_adult, is_special, manual_amount)` — returns `(base_amount, schedule_type, payment_modality)`, i.e. the recurring period fee and how it is scheduled. The quarterly branch calls `pricing_service.quarterly_price_from_monthly` rather than carrying a fourth copy of "three months minus the quarterly percentage" (v1.27.1).
- `_apply_discounts(config, base, ...)` — applies sibling and language cheque discounts. The €0.01 floor and the HALF_UP quantize come from `pricing_service.round_money` since v1.27.1; they used to be spelled out here, which made this the second of **three** copies (the payment generator and `Enrollment.save()` held the others), and the rounding has to match exactly or a half-cent intermediate stores one figure on the ficha and bills another on the invoice
- `compute_enrollment_fee(config, student, is_adult, special_fee=None, force_returning=False, this_academic_year=None)` — returns `(final_fee, returning_discount_applied)`. **`this_academic_year` must be passed by any caller that has just created the enrollment** (`enrollment.academic_year`): judged against today's year instead, a future-dated enrollment reads as the student's own prior history and wrongly wins the discount. `force_returning` is the "Antiguo alumno" checkbox — it grants the discount, never revokes one the prior enrollments already earn. `special_fee` (v1.20.0) is the form's optional **Matrícula especial (€)**: a negotiated figure, so it is returned verbatim with no returning-student discount taken off it. It is deliberately separate from `manual_amount`, which prices the *recurring* fee only — a special monthly price does not imply a special matrícula, and before v1.20.0 such an enrollment was silently charged the standard one.
- `is_returning_student(student)` / returning-student enrollment discount (v1.13) — a student who previously had an enrollment pays a reduced enrollment fee
- `close_active_enrollments(student, status, cancel_pending_periodic=False, keep_payment_type=None, cancel_from=None)` — finishes the live enrollment(s) and optionally cancels their pending **periodic** rows from `cancel_from` on (months already taught stay owed; completed money is never touched). `keep_payment_type` has **no caller** as of v1.27.1 — it preserved exactly the full-price transition-month row that made a replacement's prorated first period disappear — and is kept only because it is the right behaviour for a caller that genuinely wants a same-cadence overlap kept

#### Plan transitions (v1.27.1)

- `supersede_enrollment(student, current, *, requested_start=None, parent=None) -> date | None` — **THE** canonical plan transition, shared by all three call sites: `StudentUpdateView`'s plan change, `enroll_student` ("Nueva matrícula") and `update_enrollment_modality`. They managed the same two hazards and had drifted into three different wrong answers. In order: resolve the handover date **first** (so a refusal cannot leave a half-applied change behind), bill what the closing enrollment taught and never invoiced (`PaymentService.close_out_periods`), then close it, cancelling its pending periodic rows due on or after the handover month. Returns the date the replacement **must** start on — not always `requested_start` — or `None` when every remaining month of the course is already invoiced, in which case **nothing is written** and the caller refuses the change.
- `replicate_enrollment(source, *, start_date, payment_modality=None, schedule_type=None)` — issues a fresh `Enrollment` carrying `source`'s plan for changes that have no form behind them (today only the modality toggle). The price is re-derived from `SiteConfiguration` for the **new** cadence via `period_base_amount`, because `final_amount` is the price of one period and a month is not a quarter; a hand-priced (`special`) enrollment is carried over verbatim, since only a human may restate a negotiated figure per period.
- `change_payment_modality(student, enrollment, modality, parent=None) -> (replacement, start)` — the monthly ⇄ quarterly switch, `(None, None)` when it cannot take effect this course. One `transaction.atomic()` around `supersede_enrollment` → `replicate_enrollment` → `schedule_academic_year_payments`. The endpoint used to flip `payment_modality` on the live row and null `final_amount` so `save()` re-derived it — which kept the **original `enrollment_date`** as the anchor, so the new cadence's `billing_periods` reached back over months already *collected* under the old one. The `(student, payment_type, due month)` idempotency cannot see them (`payment_type` is one of its keys) and neither can the DB constraint (pending-only **and** same-type): Sep+Oct collected monthly, flipped to quarterly on 15 November, and the next cron run invoiced a full-price Sep–Nov quarter. The reverse — a collected quarter, then monthly — back-filled three paid months.

See [`ENROLLMENT_PAYMENT_SYSTEM.md`](../ENROLLMENT_PAYMENT_SYSTEM.md) for the two rules that pick the effective date and why the transition month is never split. The regressions are pinned by `tests/integration/test_enrollment_transition_fixes.py`, which drives the **service** rather than the endpoint wherever an exact date matters (`transition_start_date` reads `date.today()` when nothing is passed, and a test that lets it do so is the date bomb CLAUDE.md warns about twice) and uses the **elapsed** 2020-2021 course so every period has started and the counts hold on any run date.

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
- `_price_months(enrollment, config, months, effective, quarterly)` (v1.27.1) — the discount ORDER, split out of `calculate_period_amount` so a period short at **both** ends can be priced by the same code. `calculate_period_amount` expresses "the first month is partial" (one fraction, on month 0), which is all the schedule generator needs; `close_out_periods` bills a block truncated by a plan change and hands in the effective total directly. Any new amount calculation must come through it — a second copy of that order is a family charged one figure and quoted another.

#### Plan-transition helpers (v1.27.1)

- `covered_months(student)` — `{(month, year)}` that a periodic payment of this student already invoices, in **one** query and across **both** cadences. That is the whole point: the generators' own idempotency (`billed_months_map`, `pending_periods`) is keyed on `(student, payment_type, due month)` and is therefore structurally blind across a cadence change. A `monthly` row covers its due month; a `quarterly` row covers its due month **and the two before it** (the block it was priced for). Cancelled rows are **excluded** — deliberately unlike `billed_months_map`, because cancelling frees the month under the DB constraint and a superseded row must not reserve a month forever; everything else counts, `pending` included, since an invoice already sent to a family is not re-issued under a different plan. The one-month June stub over-reports (it cannot say its block was short), which can only push a plan change *later*, never re-bill a paid month.
- `transition_start_date(student, requested_start=None, closing=None) -> date | None` — the single answer to "when does a plan change take effect?". Two rules: never inside a month `covered_months()` already invoices, and never mid-month while `closing` is still teaching (the handover moves to the **1st of the following month** and the old plan keeps the transition month whole). Rule 2 is the database's judgement — splitting the month needs two pending periodic rows due in it, which `unique_pending_periodic_payment_per_month` forbids, and the alternatives are the two bugs this replaces: bill both (charged twice) or bill neither head (taught for free, permanently, because a cancelled row still occupies its month for the cron's back-fill). A request on the 1st, or for a month the closing enrollment does not teach, is honoured as-is so its first period stays prorated. `None` = nothing left to bill this course.
- `close_out_periods(enrollment, parent=None, until=None)` — bills every period the closing enrollment taught **in full** before `until`, and nothing else. `generate_payments` only visits **active** enrollments, so a month left open here becomes structurally unbillable the instant the enrollment is finished — no back-fill can reach it, because the replacement's schedule starts later. Deliberately **not** `schedule_academic_year_payments(..., as_of=until - 1 day)`, which is what the view helper it replaces did: that always issues an enrollment's first period even when it opens in the future, so the transition month was invoiced in full at the old price and the replacement's prorated first period was silently dropped by the billed-month check. Skipping is checked twice — same-cadence `billed_months` (cancelled rows included, exactly as the generators do it, so a deliberate cancellation is respected) **and** `covered_months()`, because at this moment the student may carry rows of the *other* cadence. That is conservative for a quarter (one overlapping month skips the whole block, leaving the rest to `reconcile_payment_schedule`) and conservative is the right direction: under-billing is repairable, a family charged twice is not.

### PricingService (`billing/services/pricing_service.py`)

Three **module-level** helpers were added in v1.27.1 and are the only copies of their rule. They
live in this module rather than on `PaymentService` because `billing.models` needs them too
(`Enrollment.save()`'s price fallback) and cannot import the payment service — that module imports
`billing.models` at load time.

- `round_money(value)` — floor at €0.01, quantize **HALF_UP**. THE one money rounding in billing; `PaymentService._round_money` and `EnrollmentService._apply_discounts` now delegate here, and the model carried a third copy. Every consumer must see the same cents: unquantized, the `DecimalField` save path rounds HALF_EVEN while the generator rounded HALF_UP and a dry run's `f"{amount:.2f}"` rounded HALF_EVEN again — a half-cent intermediate (quarterly + sibling on the default prices is exactly **146.205**) printed 146.20 in the preview and billed 146.21 on the invoice. The €0.01 floor is not cosmetic: `Payment.amount` and `Enrollment.final_amount` both validate `MinValueValidator(0.01)` and `objects.create()` does not run validators, so an unfloored 0.00 persisted and then sat on the ficha as an uncollectable debt
- `quarterly_price_from_monthly(monthly_fee, config)` — three months minus the configured quarterly percentage. Parameterised by the fee rather than reading `config.full_time_monthly_fee` itself, because the three callers start from different bases (the advertised price is always full-time; `Enrollment.save()` and `EnrollmentService` must apply the formula to whatever base `schedule_type` selected). Hard-coding full-time is exactly what forced the three hand-rolled copies it replaces — and `Enrollment.save()`'s copy had **omitted the discount**, so an admin- or shell-created quarterly enrollment showed 162.00 on the ficha while the generator billed 153.90. Deliberately **not** rounded: callers that persist the figure round it
- `period_base_amount(config, schedule_type, payment_modality)` — the standard price of **one** billing period (a month, or a quarter on a quarterly plan), i.e. the figure `Enrollment.final_amount` holds. Shared by `Enrollment.save()`'s fallback and `EnrollmentService.replicate_enrollment`, so a plan re-issued by the app and one created by hand in the admin cannot be priced differently
- `_euros(amount)` — the Spanish money formatter used by `payment_reminder_fees` (comma decimal, no `,00` tail). Also read by `comms.services.email_functions.cheque_idioma_fee`, so the cheque-idioma row of the reminder table cannot be formatted differently from the other five

Class methods:

- `get_config()` — cached SiteConfiguration access
- `get_monthly_fee(schedule_type)` — fee by full_time/part_time/adult_group
- `get_enrollment_fee(is_adult)` — child vs adult enrollment fee
- `calculate_quarterly_price()` — a thin wrapper over `quarterly_price_from_monthly(config.full_time_monthly_fee, config)`, so the advertised figure and the one derived from a part-time or adult base cannot drift
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
- `generate_tax_certificate(...)` — annual certificate for a parent's tax return. **Grouped by `student_id`, not by name** (v1.27.1): two siblings called the same thing — the academy has had them, and a re-registered student can share a name with a cousin — were merged into ONE block with ONE subtotal, on a document the family files with the tax authority. The name rides along in the entry for the heading; the key is what identifies a person
- `generate_student_payment_history(student, payments, title_suffix="")` (v1.15) — a student's full payment history: concept, type, due date, **payment date**, **method** and status per row, with collected and outstanding totalled separately. Served by `student_payments_pdf`

- Academy details come from `SiteConfiguration`'s five `academy_*` fields (**real columns** as of v1.27.1 / migration `0013`), with the `AcademyInfo` defaults as a fallback. Before that the fields did not exist, every `getattr` fell through, and the **CIF was blank on every tax certificate the academy had ever issued** — on a document that asserts IRPF deductibility. The CIF is printed in the header whenever it is populated and **omitted rather than printed empty** when it is not; the `or <default>` fallback is kept deliberately, so a field an admin has not filled in yet still yields a usable document instead of an empty letterhead
- `_grid_table(...)` / `_fit_widths(widths_mm)` (v1.27.1) — the bordered data table every document draws, and the millimetre→point conversion that scales it into the frame. The same `TableStyle` block was written out **five** times with small divergences and nobody was comparing them, which is how two tables ended up declaring more than the printable width (`_FRAME_WIDTH_MM = 174`: A4 is 210 mm and `_build_pdf` takes 18 mm off each side). Reportlab does not refuse an over-wide table — it draws the surplus past the right edge of the page — so the last column ("Importe (€)", "En espera") was simply **cut off on print** and nowhere else. `_fit_widths` is the net that keeps the next edit from re-introducing it

All text reaching a reportlab `Paragraph` goes through `_md()`. Paragraph parses a mini-HTML
dialect, so an unescaped name like `O<Brien` raised `paraparser: syntax error` and killed generation
outright. Since v1.27.1 that includes **every `academy.*` value** — they are free text an admin
types into `/management/` and they land in a `Paragraph`, so an academy name containing `&` or `<`
would kill every document at once.

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
- `LIVE_PAYMENT_STATUSES` (`pending`, `completed`) — money the academy still expects or already has; the single filter for "esperado". **Not to be conflated with `billing.models.UNCOLLECTED_PAYMENT_STATUSES`** (`pending` alone, v1.27.1), which is money not *yet collected* — the chase list. Using the first where the second belongs puts every up-to-date family on that list; using the second for "esperado" under-reports expected revenue by everything already banked. It lives in `models.py` rather than here because its two readers (`Enrollment.payment_totals()` and `EnrollmentAdmin`'s annotations) are the reason it exists. `PERIODIC_PAYMENT_TYPES` (`monthly`, `quarterly`) — the types the recurring schedule issues, as opposed to the one-off matrícula and ad-hoc rows. Moved here in v1.26.1 from a private copy in `reconcile_payment_schedule`; `billed_months_map` and the `unique_pending_periodic_payment_per_month` constraint both key off it (the constraint repeats the tuple as a literal, because a migration can only compare literals — keep the two in step)
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
| `PaymentAdmin` | Student/parent links (both `None`-safe — an adult student has no guardian), amount and status columns, CSV export through `csv_safe_row`. **Bulk actions (v1.26.0):** `mark_as_completed` now touches only *pending* rows and queues a receipt for each. It was a bare `queryset.update(payment_date=today)`, which rewrote the date on already-completed payments — moving settled money into the current month in every income report, the regression `quick_complete_payment` short-circuits on — and sent nothing. Reopening a payment clears `payment_date`, because every income figure filters on it. **v1.27.1:** `_bulk_set_status` **refuses to void a COMPLETED payment** — "Marcar como fallidos" and "Cancelar los pagos seleccionados" silently dropped collected income, in a month very likely already closed and reported, and neither status describes what happened (it did not fail, and it was not withdrawn from the schedule). It returns `(changed, protected)` and `_report_bulk_status` names up to five of the protected rows in a Spanish warning pointing at "Marcar como pendientes", which is the auditable route back and has to pass the pending-per-month constraint — exactly the check a re-collection needs. This is the mirror image of `mark_as_completed` skipping non-pending rows. Every fieldset legend and flash message on this ModelAdmin is Spanish now (`Titularidad`, `Detalles del pago`, `Fechas`, `"N pagos marcados como completados."`, `"… d de retraso"`); several were still English in the middle of a Spanish UI |
| `EnrollmentAdmin` | Four-state `payment_status_display` (v1.26.0): overdue / up-to-date-with-an-open-period / settled / not-yet-billed. It was a paid-unpaid pair driven by `is_paid`, which compared a year of collected money against one period's price. The **Billing plan** fieldset (`academic_year`, `payment_modality`, `is_sibling_discount`, `has_language_cheque`) was added at the same time — none of those fields were on any form, and `academic_year` is what `generate_payments` filters on, so a wrong value meant silently never billed with nowhere to fix it. **v1.27.1:** the three readonly figures are display methods (`up_to_date_display`, `overdue_display`, `outstanding_display`) sharing **one** `payment_totals()` per object via `_totals()`, instead of the `is_up_to_date` / `overdue_amount` / `outstanding_amount` model properties — each of which calls `payment_totals()`, so rendering the change form ran the same aggregate three times. The changelist keeps its annotations and `_totals()` prefers them; the model properties are left alone, since they are read all over the app on instances the caller expects to be live. The annotations now filter on `UNCOLLECTED_PAYMENT_STATUSES`, read from the same constant `payment_totals()` uses — this annotation is an optimisation *of* that method, and a silent disagreement shows the admin one debt figure while every other page shows another. Legends `Información adicional` / `Sistema` translated |
| `EnrollmentTypeAdmin` | Registered bare until v1.26.0. The four rows are resolved **by name** and a missing one raises `ValueError`, which blocks every enrollment of every kind — and a row not yet referenced by an enrollment had no `PROTECT` guarding it, so it was one click from deletion. Delete is refused for the four required names, `name` is read-only once the row exists (`choices` stops you inventing a value, not swapping `special` onto the row resolved as `adults`), and the matrícula amounts are visible in the list |
| `SiteConfigurationAdmin` | Singleton — add is refused once a row exists, delete always. Deliberately has **no `fieldsets`**: a field absent from a fieldset is *unreachable*, not merely hidden, and this is the one row holding every live price — so the five `academy_*` columns appear the moment they exist. `academy_cif` is surfaced in `list_display` because it ships empty and decides whether a tax certificate is usable (v1.27.1) |
| `ExpenseAdmin` | List/filter/search; `generated_from` read-only. The three recurrence fields carry Spanish `verbose_name`s and Spanish `help_text` as of v1.27.1 — `help_text` is rendered on this form, so it is user-facing, and it stated a 1-28 bound the validator never enforced |

## URL Patterns (billing/urls.py)

Payment CRUD, enrollment API, management panel, expenses (create / **update** (v1.20.0) / delete),
reports, search/statistics, Stripe endpoints, CSV/Excel export, per-student payment-history PDF
(v1.15). **24 URL patterns** total.

## Cross-App Communication

- **Depends on**: students (FK to Student, Parent in Enrollment and Payment models)
- **Depended on by**: core views (dashboard shows payment stats), comms (email functions reference Payment for tax certificates)
- **Exports used by core**: `SiteConfiguration`, `Enrollment`, `Payment`, `current_academic_year`, service classes
