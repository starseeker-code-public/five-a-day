# Enrollment & Payment System - Five a Day

## Academic Year

- Spans from the **first Monday on or after September 14th** to the **last Friday of June**.
- Annotated with two years, e.g. `2025-2026`.
- In student creation, only the years appear (e.g. "2025-2026"); actual dates are computed when saved to database.

---

## Enrollments

An enrollment represents a student being "inside the system" for an academic year. One enrollment per academic year per student.

### Start date (v1.26.8)

The enrollment form carries a **Fecha de inicio** (`EnrollmentForm.start_date`, blank = today). It is
the day the student actually STARTS, which need not be the day the ficha is created, and it becomes
`Enrollment.enrollment_date` — the single date everything downstream reads:

- the **academic year** is `current_academic_year(start_date)`, not of today;
- the **matrícula** Payment falls due on the last day of the month the enrollment *starts*;
- **billing begins** at that month (`PaymentService.billing_periods`), and the first period is
  prorated from that day (`proration_fraction`).

So a family signing up on 3 September for a 1 November start is billed from November, with a full
November — not a prorated September. The same helper (`_create_enrollment_fee_payment`) issues the
matrícula for both entry points, the student-creation page and the "Nueva matrícula" modal, so the
fee, the returning-student discount and the concept wording cannot drift between them.

> **v1.27.1** — for an enrollment that REPLACES a live one, the date typed is a *request*, not a
> guarantee: `PaymentService.transition_start_date()` may move it forward, and the endpoint reports
> the date actually used (`effective_start`). See
> [Changing the plan SUPERSEDES the enrollment](#changing-the-plan-supersedes-the-enrollment-v1271).
> A first enrollment, and any enrollment for a course whose months are all still unbilled, is
> unaffected.

### "Antiguo alumno" override (v1.26.8)

The returning-student matrícula is normally detected automatically — the student has an `Enrollment`
for an earlier academic year. The form's **Antiguo alumno** checkbox (`is_returning_student`) lets an
admin assert it for a `Student` row that has no such history: someone re-registering after years
away, or promoted off the waiting list, which creates a fresh row. It is OR-ed with the automatic
detection, so it can only **add** the discount, never revoke one, and it does not outrank `special`
or `adults` when the matrícula category is resolved. It is pre-ticked when the ficha is being created
from a waiting-list entry that already carries enrollments.

The discount is judged against **the enrollment's own academic year**, not today's — otherwise a
future-dated enrollment would read as the student's own prior history and win the discount by itself.

### Changing the plan SUPERSEDES the enrollment (v1.27.1)

There are three ways to change a student's plan mid-course, and all three now go through one
service method — `EnrollmentService.supersede_enrollment(student, current, requested_start, parent)`:

| Entry point | Trigger |
|-------------|---------|
| `StudentUpdateView` | editing the ficha in a way that changes the plan (`_enrollment_plan_changed`) |
| `enroll_student` (`POST /api/students/<id>/enroll/`) | the "Nueva matrícula" modal, the book icon on the list |
| `update_enrollment_modality` (`POST /api/students/<id>/enrollment/modality/`) | the monthly ⇄ quarterly toggle on the student ficha |

**Nothing is ever edited in place.** The outgoing enrollment is closed (`status="finished"`) and
KEPT as the record of what it billed; a replacement `Enrollment` is created with a new
`enrollment_date`. `enrollment_date` is the anchor *every* billing decision reads
(`billing_periods`, `proration_fraction`, the matrícula due date), so mutating a plan on the live
row silently re-interprets a schedule that has already been invoiced against the old anchor.

The transition happens in a fixed order, inside one transaction:

1. **Resolve the handover date first** (`PaymentService.transition_start_date`). If it comes back
   `None`, *nothing is written* and the caller refuses the change with a Spanish message — see
   "Refusal" below.
2. **Bill what the closing enrollment taught and never invoiced**
   (`PaymentService.close_out_periods`). `generate_payments` only visits **active** enrollments, so
   a month left open here can never be back-filled: the replacement's schedule starts later than it.
   A quarter truncated by the handover is billed for its taught months only, due on the last of them.
3. **Close it**, cancelling its *pending* periodic rows due on or after the handover month. Months
   before the handover stay owed (a student taught in September owes September) and **completed
   money is never touched**.
4. **Create the replacement** anchored on the effective date and issue its first period immediately
   (idempotent against the cron), so the ficha does not sit with no payment under the new plan.

**Two rules decide the effective date**, and both exist because a month can only be billed once:

- **Never inside a month another period already invoices.** The first candidate teaching month not
  in `PaymentService.covered_months(student)` wins. That set reads **both cadences** — which is the
  whole point (see the correction under "Automatic Payment Generation").
- **Never mid-month while the closing enrollment is still teaching.** If the old plan taught the
  first part of the requested month, the handover moves to the **1st of the following month** and
  the old plan keeps the transition month **whole**. Splitting it would need two pending periodic
  rows due in one month, which `unique_pending_periodic_payment_per_month` forbids — and the two
  alternatives are the bugs this replaces: bill both (the month charged twice) or bill neither head
  (days taught for free, permanently, because a cancelled row still occupies its month for the
  cron's back-fill).

A request on the **1st**, or for a month the closing enrollment does not teach (a re-enrollment for
next course, a backdated start), is honoured as-is — there is no split to avoid, so the first
period stays prorated.

**Refusal.** When every remaining teaching month of the course is already invoiced there is nothing
left to bill, so the change cannot take effect without re-charging a month the family has already
been asked to pay. All three endpoints refuse (400 JSON, or a `messages.warning` on the ficha) and
tell the admin to cancel the affected pending charges first.

**Nothing is silent.** All three endpoints report the effective date — `effective_start` in the JSON,
an `info` flash on the ficha when it differs from what was typed — and the modality change writes a
`HistoryLog` entry of its own action, `enrollment_modality_changed`.

`EnrollmentService.replicate_enrollment` is the "no form behind it" case (today only the modality
toggle): it copies the source plan and re-derives the price from `SiteConfiguration` **for the new
cadence**, because `final_amount` is the price of one period and a month is not a quarter. A
hand-priced (`special`) enrollment is the exception and is carried over verbatim — only a human may
restate a negotiated figure per period.

> **What this replaced.** The modality endpoint used to flip `payment_modality` on the live row and
> null `final_amount` so `save()` re-derived it. That kept the ORIGINAL `enrollment_date`, so the
> new cadence's `billing_periods` reached back over months already **collected** under the old one —
> and the `(student, payment_type, due month)` idempotency cannot see them, because `payment_type`
> is one of its keys. September and October collected monthly, flipped to quarterly on 15 November,
> and the next `generate_payments` run invoiced a full-price Sep–Nov quarter: two months collected
> twice, with the database constraint unable to help (pending-only, and same-type). The reverse —
> a collected quarter, then monthly — back-filled three paid months. The `StudentUpdateView` /
> `enroll_student` helper had the mirror-image defect: it closed the old plan with
> `schedule_academic_year_payments(..., as_of=new_start - 1 day)`, which always issues an
> enrollment's *first* period, so the transition month was invoiced in **full at the old price** and
> the replacement's prorated first period was then silently dropped by the billed-month check.

### Enrollment Fees (once per academic year)

| Student Type       | Fee   | Notes                                            |
|--------------------|-------|--------------------------------------------------|
| New child          | 40€   | Default enrollment fee                           |
| Returning child    | 20€   | Old student discount: -20€ (already had an enrollment) |
| Adult (18+)        | 20€   | Fixed, no discounts                              |
| Special            | manual | **Matrícula especial (€)** on the enrollment form, charged verbatim (v1.20.0) |

The four rows above are exactly the four `EnrollmentType` categories — `new_student`, `returning_student`, `adults`, `special`. An `EnrollmentType` is a **matrícula category, not a payment cadence**; the cadence lives on `Enrollment.payment_modality`.

A special *matrícula* is independent of a special *cuota*: **Precio manual (€)** prices the recurring fee, **Matrícula especial (€)** prices the one-time enrollment. Setting the first does not imply the second — left blank, the standard matrícula applies (returning-student discount included). A hand-set matrícula is a negotiated figure, so no discount is taken off it, and it is not stored on `Enrollment` — the `payment_type="enrollment"` Payment row is the record.

### June Discount

- Students who **complete the academic year** (are still active in June) get a **-20€ discount** on their June payment.
- Applies regardless of when they were enrolled.
- Does **not** apply to adults.

---

## Payment Modalities

Each enrollment has a **payment modality** (monthly or quarterly), changeable at any time from the
student detail view. Since **v1.27.1** that change does not mutate the enrollment: it
**supersedes** it, taking effect on the first month no periodic payment of *either* cadence already
invoices, and it can be refused outright. See
[Changing the plan SUPERSEDES the enrollment](#changing-the-plan-supersedes-the-enrollment-v1271).

### Monthly Payments

| Schedule          | Description        | Price  |
|-------------------|--------------------|--------|
| 2 days/week       | Full-time (default)| 54€    |
| 1 day/week        | Part-time          | 36€    |

Monthly payments are due every month from **September** to **June**.

**Monthly Discounts:**
- **Sibling discount**: -5% for the younger sibling (both share the same parent)
- **Cheque idioma** (language ticket): -20€/month flat discount. Students with this must be reported to the government monthly.
- **June discount**: -20€ for completing the academic year

### Quarterly Payments

Quarters are **anchored to the enrollment month** (v1.22.0), not to a fixed calendar:
consecutive 3-month blocks starting from the month the student joins, with a short final
block when the academic year ends first. Joining in September gives Sep–Nov, Dec–Feb,
Mar–May and a one-month June stub; joining 12 December gives Dec–Feb, Mar–May and June.
Each block is created on its first day and falls **due on its last** day.

> The fixed Oct/Jan/Apr calendar this replaced had two silent revenue holes — September
> fell outside every quarter, and a mid-quarter joiner's first months were never billed.
> The `QUARTERS` constant that encoded it survived, unread, until v1.26.0.

Quarterly amount = 3 months × monthly fee × 0.95 (5% discount), **then the same discounts a
monthly student would receive**.

- **Sibling discount** applies (percentage, on the discounted quarterly total).
- **Language cheque** applies per covered month — three cheques on a full quarter, one on a
  June stub (`calculate_period_amount` scales by the months the block actually covers).
- **June discount** applies to whichever block **contains June**.
- **Adult groups** keep the flat rate: quarterly percentage only, no further discounts — matching
  `calculate_monthly_amount`, which returns the adult base fee untouched.
- Quarterly students are notified at the **start of each quarter**.

> Changed in **v1.15.0**. Previously only the 5% quarterly discount was applied, so a quarterly
> student with a sibling discount or a language cheque was billed the full amount — and the
> `Enrollment` row (which *did* apply them, via `EnrollmentService._apply_discounts`) disagreed with
> the `Payment` rows generated from it. `PaymentService.calculate_quarterly_amount` now mirrors that
> method exactly, and its `quarter_due_month` argument — previously unused — is what carries the June
> discount into Q3.
- Monthly students are notified **each month**.

### Special Payments

- The admin types the price by hand. It is stored on the enrollment (`enrollment_amount` / `final_amount`) and is the **per-period** figure: per month, or per quarter for a quarterly special.
- `schedule_type` is only the timetable the student actually attends — it is **not** a price band. A special enrollment can be monthly or quarterly, 1 day or 2 days a week.
- Every payment of the year is billed at that amount via `PaymentService.hand_priced_amount()`. Sibling / language-cheque / June discounts are **not** layered on top: `EnrollmentService._apply_discounts` already folded whatever was ticked into the stored figure at creation.
- Before v1.20.0 both generators re-derived the fee from `SiteConfiguration`, so the ficha showed the custom price while every payment charged the standard 1-day / 2-day rate.

---

## Adult Students (18+)

- **Enrollment**: 20€
- **Schedule**: Only 1 day/week monthly at **60€**
- **No discounts** of any kind
- **No parents**: adults have their own email and phone
- **Don't have**: allergies, GDPR, parents, school fields
- Student creation for adults skips the parent creation step

---

## Automatic Payment Generation

Pending (due, not paid) periodic payments are created three ways, and all three are kept consistent (idempotent — never duplicated):

**1. At enrollment (the period they joined).** When a student is enrolled (via the create-student flow, waiting-list assignment, or the "Nueva matrícula" modal), in addition to the one-off enrollment fee, `PaymentService.schedule_academic_year_payments()` creates the periodic payment for the period they joined — **prorated** for the days of that month already gone. The first period is always issued even if it opens later, so a student enrolled in August still has their September fee on the ficha that day.

**Periods are created on their FIRST day and fall due on their LAST.** Monthly students get one period per teaching month (Sep–Jun). Quarterly students get consecutive 3-month blocks **anchored to the month they enrolled** — joining 12 December gives Dec-Feb (due 28 Feb), Mar-May (due 31 May) and a one-month June stub (due 30 Jun). This replaced a fixed Oct/Jan/Apr calendar that had two silent revenue holes: a 12-December joiner was never billed for December at all, and September fell outside every quarter so quarterly students never paid for it.

**Only the first period is prorated.** `proration_fraction()` bills the days remaining in the joining month, counting the join day — 15 September on a 30-day month is 16/30, 12 December on 31 days is 20/31. Every later month, and every later period, is billed in full. Within a quarter only the first month is reduced, so the block is worth `(2 + fraction)/3`. The payment's concept is marked `(parcial)` and the creation form previews the amount from the same helper.

**2. On the 1st of each month (Celery / Cloud Run Job).** The `generate_payments` management command opens every period that has started since the last run — the new month for monthly students, the new block for quarterly ones. It calls exactly the same `schedule_academic_year_payments()` the enrollment form does, so there is one code path and the two can never disagree.

**3. On a plan transition (v1.27.1).** Changing a plan closes one enrollment and opens another, and
both halves bill: `PaymentService.close_out_periods()` issues the months the *closing* enrollment
taught and never invoiced (at its own price, before it is finished — the cron only visits **active**
enrollments, so a month left open there is unbillable forever), and the replacement's first period
is issued immediately rather than waiting for the 1st, so the ficha does not sit with no payment
under the new plan. `close_out_periods` deliberately does **not** go through
`schedule_academic_year_payments`: that always issues an enrollment's *first* period even when it
opens in the future, which is what made the transition month get billed in full at the old price
while the replacement's prorated period was silently dropped. See
[Changing the plan SUPERSEDES the enrollment](#changing-the-plan-supersedes-the-enrollment-v1271).

Since v1.26.0 the question *"should this period be billed yet?"* is answered in exactly one place, `PaymentService.pending_periods()`, which both the real run and `generate_payments --dry-run` consume. They used to apply the rules separately and had already drifted on both of them — the preview dropped a first period opening in the future, and matched existing payments on the exact due date rather than payment type + due month/year — so `--dry-run` could disagree with the run it was previewing.

It also **back-fills**: any period that has started and has no payment is created, so a run the scheduler missed is repaired on the next one instead of leaving a month permanently unbilled.

Because both paths match on `(student, payment_type, due-date month/year)` before creating, nothing is ever charged twice **by the same cadence**.

> **Corrected in v1.27.1 — that key is blind across a cadence change.** `payment_type` is one of
> its three components, so a `completed` monthly September is *invisible* to a newly-anchored
> quarter, and the database constraint cannot help either (it is pending-only **and** same-type).
> That is exactly how a monthly ⇄ quarterly switch re-billed months already collected. The
> question "does any periodic payment already invoice this month?" now has its own answer,
> `PaymentService.covered_months(student)`: **one** query over **both** cadences, where a
> `quarterly` row covers its due month *and the two before it* (the block it was priced for).
> Every plan transition is judged against it — see
> [Changing the plan SUPERSEDES the enrollment](#changing-the-plan-supersedes-the-enrollment-v1271).
>
> The two sets differ deliberately, and the difference is the point:
>
> | | `billed_months` / `billed_months_map` | `covered_months` |
> |---|---|---|
> | Cadence | one `payment_type` at a time | **both** |
> | Cancelled rows | **count** as occupying the month | **excluded** |
> | Used by | the generators' idempotency | plan transitions |
>
> A cancelled row keeps its month reserved for the generators (so a payment an admin soft-deleted
> is not re-created by the cron) but frees it for a transition (so a superseded row cannot reserve
> a month forever — a cancelled row holding its month is what made a mis-billed transition
> unrepairable). The one-month June stub reports the two months before June as covered too,
> because a row cannot say whether its block was short; that can only push a plan change *later*,
> never re-bill a month that was paid, which is the direction that cannot cost a family money.

**Since v1.26.1 that guarantee is the DATABASE's, not just Python's.** The match above is a read-then-write, and Cloud Run Jobs retry on failure — so two overlapping `generate_payments` runs could both pass the check and bill a family twice, which is precisely the failure this whole schedule design assumes cannot happen. `payments.unique_pending_periodic_payment_per_month` is a partial unique index on `(student, payment_type, EXTRACT(YEAR/MONTH FROM due_date))`, and it is deliberately **narrower** than the Python check:

- **Only `pending` rows.** The generators only ever create `pending`, so pending-vs-pending is the whole of the race they can lose, and two pending periodic rows for one month is the double-billing symptom and nothing else. Including `completed` would forbid states the academy really has — a month paid part in cash and part by transfer, or a correction billed after a partial collection.
- **Only `monthly` / `quarterly`.** A student can legitimately owe several `enrollment` or `other` payments in one month.
- **Only one `payment_type` at a time.** The index keys on it, so it cannot see a monthly row when a quarterly one is created for the same month — the blindness `PaymentService.covered_months()` exists to cover (v1.27.1).
- **Cancelling frees the month again**, which is what lets `reconcile_payment_schedule` supersede a stale row with one due in the same month. That command therefore cancels *before* it creates.
- **A freed month is why a cancelled payment can never be completed** (v1.27.1). The schedule may already have re-billed it, and the constraint is pending-only, so nothing would stop a `completed` duplicate. `Payment.assert_completable()` refuses it — and refuses a **refunded** row too, where the money has already gone back to the family. See [Completing a payment](#completing-a-payment).

`schedule_academic_year_payments` wraps each create in a transaction and swallows the resulting `IntegrityError`: losing the race means the payment now exists, which is the outcome wanted, and one lost race must not abort the remaining periods or the rest of the cron. In the UI the constraint surfaces through `full_clean()` as a Spanish message telling the admin to edit or cancel the existing payment.

Migration `billing/0010` **refuses to apply** against a database that already holds duplicates, naming the offending students, rather than dying with a bare Postgres error part-way through a deploy. If it blocks a deploy, that database really is double-billed: repair it with `manage.py reconcile_payment_schedule` (dry run by default) and re-run.

Cancelled payments count as existing, so a payment an admin soft-deleted through `deactivate_payment` is **not** re-created — by the cron or by `reconcile_payment_schedule`. (That is `billed_months`; a *plan transition* reads `covered_months`, which deliberately excludes cancelled rows — see the table above.)

> **Resolved in v1.22.0.** This section previously carried a "known gap" noting that a quarterly student's September went unbilled, because Q1 ran Oct–Dec under the fixed calendar. Anchoring quarters to the enrollment month closed it: the first block starts in the month the student joined, so September is inside it for anyone enrolling in September. The `QUARTERS` list that encoded that calendar (and its never-read `includes_sept` flag) was removed from `billing/constants.py` in v1.26.0.

### Payment Amount Calculation

**Monthly (children, 2 days/week):**
```
base = full_time_monthly_fee (54€)
- sibling_discount if applicable (-5%)
- language_cheque_discount if applicable (-20€)
- june_discount if June and completing year (-20€)
```

**Monthly (children, 1 day/week):**
```
base = part_time_monthly_fee (36€)
- sibling_discount if applicable (-5%)
- language_cheque_discount if applicable (-20€)
- june_discount if June and completing year (-20€)
```

**Quarterly (children):**
```
base = 3 × monthly_fee × 0.95
- sibling_discount % if applicable
- language_cheque_discount × 3 if applicable
- june_discount if Q3 (due month = April, covers April–June)
minimum 0.01€
```

**Adult monthly:**
```
base = adult_group_monthly_fee (60€)
No discounts.
```

**Special (monthly or quarterly):**
```
amount = enrollment.final_amount   (the admin's hand-set per-period price)
No further discounts — they were already applied when the enrollment was created.
minimum 0.01€
```
Both generators short-circuit here, so any queryset feeding them needs `select_related("enrollment_type")`. The predicate "is this a hand-priced enrollment?" is the single property **`Enrollment.is_hand_priced`** (v1.27.1) — it was inlined as `enrollment_type.name == "special"` at five sites, each deciding on its own whether a negotiated price may be re-derived from `SiteConfiguration`, and one copy disagreeing is a family billed the standard rate against a price the academy agreed with them.

### Shared money helpers (v1.27.1)

Three formulas were written out more than once each, and money rounded two different ways in the same
transaction. They now live in `billing/services/pricing_service.py` and are the **only** copies:

| Helper | Rule |
|--------|------|
| `round_money(value)` | Floor at €0.01, quantize **HALF_UP**. THE one money rounding in billing. |
| `quarterly_price_from_monthly(monthly_fee, config)` | Three months of `monthly_fee` minus the configured quarterly percentage. Parameterised by the fee, because the advertised price is always full-time while `Enrollment.save()` and `EnrollmentService` must apply it to whatever base `schedule_type` selected. |
| `period_base_amount(config, schedule_type, payment_modality)` | Standard price of **one** billing period — a month, or a quarter on a quarterly plan. This is the figure `Enrollment.final_amount` holds. |

`PaymentService._round_money` and `EnrollmentService._apply_discounts` delegate to `round_money`;
`Enrollment.save()`'s price fallback and `EnrollmentService.replicate_enrollment` both go through
`period_base_amount`. `PricingService.calculate_quarterly_price` is now a thin wrapper.

Two real bugs this closes: `Enrollment.save()`'s quarter copy had originally **omitted the discount**,
so an admin- or shell-created quarterly enrollment showed 162.00 on the ficha while the generator
billed 153.90; and unquantized, the `DecimalField` save path rounds HALF_EVEN while the payment
generator rounded HALF_UP — a half-cent intermediate (quarterly + sibling on the default prices
lands exactly on **146.205**) stored 146.20 on the ficha and billed 146.21 on the invoice.

The €0.01 floor is not cosmetic: `Payment.amount` and `Enrollment.final_amount` both validate
`MinValueValidator(0.01)`, and `objects.create()` does not run validators — so an unfloored 0.00
persisted happily and then sat on the ficha as an uncollectable debt.

---

## Payment Statuses

Two status sets answer two different questions, and conflating them is a real regression:

| Constant | Statuses | Question |
|----------|----------|----------|
| `billing.constants.LIVE_PAYMENT_STATUSES` | `pending`, `completed` | Money the academy still **expects** — collected or not. This is "esperado". |
| `billing.models.UNCOLLECTED_PAYMENT_STATUSES` (v1.27.1) | `pending` | Money invoiced and **not yet collected**. This is the chase list. |

`Enrollment.payment_totals()` and `EnrollmentAdmin.get_queryset`'s annotations (one is an
optimisation of the other, so they must agree exactly) both read `UNCOLLECTED_PAYMENT_STATUSES`.
Folding `completed` into it would report already-banked money as outstanding debt and put every
**up-to-date** family on the chase list; the reverse — using the uncollected set for "esperado" —
under-reports expected revenue by everything already collected. Cancelled, failed and refunded
money is outside both.

---

## Student Types in UI

### /students View Filters
- **All**: Show all students
- **Children**: Show students where `is_adult = False`
- **Adults**: Show students where `is_adult = True`

### Student Creation Buttons
1. **Nuevo Estudiante** (default): Creates parent first, then student (existing flow)
2. **Estudiante Adulto**: Creates adult student directly (no parent needed)
3. **Estudiante con padre existente**: Search for existing parent, then create student

---

## /payments View

### Filter by Payment Type
- Monthly 2 days/week
- Monthly 1 day/week
- Quarterly

### Filter by Status
- Not completed (pending, overdue, failed)
- All

### Quick Payment Completion
- Each unpaid payment row shows a payment icon (instead of the student icon)
- Clicking the icon reveals a dropdown with payment methods (Cash, Transfer, Credit Card)
- Selecting a method immediately completes the payment with that method
- The trigger is hidden on cancelled rows, and the endpoint refuses them anyway — see below

### Completing a payment

A payment may only be completed **from `pending`**. `Payment.assert_completable()` (v1.27.1) is the
one rule, and `Payment.DEAD_STATUSES = ("cancelled", "refunded")` is the one list:

- **Cancelled** money was withdrawn from the schedule, and cancelling *frees the month* under
  `unique_pending_periodic_payment_per_month` — so the schedule may already have re-billed it, and
  that constraint is pending-only, which would not stop a `completed` duplicate.
- **Refunded** money went back to the family. Completing it re-books income that no longer exists,
  rewrites `payment_date` to today (moving it into this month's figures) and emails a receipt for a
  refund.

`Payment.clean()` runs the check on every write path that validates, re-reading the *stored* status
because the instance's own field has been overwritten by then. `quick_complete_payment` runs it on
the row **as loaded, before mutating it**, so it can answer with a 400 and the model's own Spanish
wording. Its previous inline status list named only `cancelled` — and `payment.save()` never runs
`clean()` — which is how the endpoint resurrected refunds. Re-collecting genuinely dead money means
creating a **new** payment.

The admin's bulk actions enforce the mirror image: `mark_as_completed` touches only *pending* rows,
and `_bulk_set_status` refuses to move a **completed** payment to `failed` or `cancelled` (that
money has been collected and counted as income in a month very likely already closed and reported).
"Marcar como pendientes" is the auditable route back.

---

## Configuration (via /management)

All prices and discounts are managed through the `/management` view and stored in `SiteConfiguration`:

- `children_enrollment_fee`: 40€
- `adult_enrollment_fee`: 20€
- `full_time_monthly_fee`: 54€ (2 days/week)
- `part_time_monthly_fee`: 36€ (1 day/week)
- `adult_group_monthly_fee`: 60€
- `old_student_discount`: 20€ (flat)
- `june_discount`: 20€ (flat)
- `language_cheque_discount`: 20€ (flat, monthly)
- `quarterly_discount`: 5% (percentage)
- `sibling_discount`: 5% (percentage, monthly)

### Academy fiscal details (v1.27.1)

`SiteConfiguration` also carries the five fields the PDFs print in their letterhead. They are
edited from the same `/management` panel (`update_site_config`) and are **real columns** as of
migration `billing/0013`:

| Field | Default |
|-------|---------|
| `academy_name` — Nombre fiscal de la academia | `Five a Day English Academy` |
| `academy_cif` — CIF/NIF | **empty** |
| `academy_address` — Dirección | `C/ Hermanos Jiménez 25 · 02004 Albacete` |
| `academy_phone` — Teléfono | `967 049 096` |
| `academy_website` — Web | `www.fiveadayenglish.com` |

`pdf_service._get_academy_info()` had always read these five names off the config with
`getattr(..., "")` — and **none of them existed**, so every value fell through to the hard-coded
`AcademyInfo` defaults and `academy_cif` fell through to the empty string. The **tax certificate
asserts IRPF deductibility while naming no CIF**, which is precisely the field that makes it
deductible: the document was cosmetically fine and fiscally useless, and there was nowhere in the
app to correct it. The CIF is now printed in the header whenever it is populated (omitted, not
printed blank, when it is not), and it is surfaced in `SiteConfigurationAdmin.list_display` because
it ships empty and decides whether a certificate is usable.

All five are `blank=True` — `update_site_config` runs `full_clean()`, and a non-blank `CharField`
would 400 **every price edit** until somebody filled these in. The defaults mirror `AcademyInfo`, so
a fresh install keeps producing the document it produces today, and the `or <default>` fallback in
`_get_academy_info()` is kept deliberately: a field an admin has not filled in yet must still yield
a usable document rather than an empty letterhead.
