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

Each enrollment has a **payment modality** (monthly or quarterly), changeable at any time from the student detail view.

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

Pending (due, not paid) periodic payments are created two ways, and the two are kept consistent (idempotent — never duplicated):

**1. At enrollment (the period they joined).** When a student is enrolled (via the create-student flow or waiting-list assignment), in addition to the one-off enrollment fee, `PaymentService.schedule_academic_year_payments()` creates the periodic payment for the period they joined — **prorated** for the days of that month already gone. The first period is always issued even if it opens later, so a student enrolled in August still has their September fee on the ficha that day.

**Periods are created on their FIRST day and fall due on their LAST.** Monthly students get one period per teaching month (Sep–Jun). Quarterly students get consecutive 3-month blocks **anchored to the month they enrolled** — joining 12 December gives Dec-Feb (due 28 Feb), Mar-May (due 31 May) and a one-month June stub (due 30 Jun). This replaced a fixed Oct/Jan/Apr calendar that had two silent revenue holes: a 12-December joiner was never billed for December at all, and September fell outside every quarter so quarterly students never paid for it.

**Only the first period is prorated.** `proration_fraction()` bills the days remaining in the joining month, counting the join day — 15 September on a 30-day month is 16/30, 12 December on 31 days is 20/31. Every later month, and every later period, is billed in full. Within a quarter only the first month is reduced, so the block is worth `(2 + fraction)/3`. The payment's concept is marked `(parcial)` and the creation form previews the amount from the same helper.

**2. On the 1st of each month (Celery / Cloud Run Job).** The `generate_payments` management command opens every period that has started since the last run — the new month for monthly students, the new block for quarterly ones. It calls exactly the same `schedule_academic_year_payments()` the enrollment form does, so there is one code path and the two can never disagree.

Since v1.26.0 the question *"should this period be billed yet?"* is answered in exactly one place, `PaymentService.pending_periods()`, which both the real run and `generate_payments --dry-run` consume. They used to apply the rules separately and had already drifted on both of them — the preview dropped a first period opening in the future, and matched existing payments on the exact due date rather than payment type + due month/year — so `--dry-run` could disagree with the run it was previewing.

It also **back-fills**: any period that has started and has no payment is created, so a run the scheduler missed is repaired on the next one instead of leaving a month permanently unbilled.

Because both paths match on `(student, payment_type, due-date month/year)` before creating, nothing is ever charged twice.

**Since v1.26.1 that guarantee is the DATABASE's, not just Python's.** The match above is a read-then-write, and Cloud Run Jobs retry on failure — so two overlapping `generate_payments` runs could both pass the check and bill a family twice, which is precisely the failure this whole schedule design assumes cannot happen. `payments.unique_pending_periodic_payment_per_month` is a partial unique index on `(student, payment_type, EXTRACT(YEAR/MONTH FROM due_date))`, and it is deliberately **narrower** than the Python check:

- **Only `pending` rows.** The generators only ever create `pending`, so pending-vs-pending is the whole of the race they can lose, and two pending periodic rows for one month is the double-billing symptom and nothing else. Including `completed` would forbid states the academy really has — a month paid part in cash and part by transfer, or a correction billed after a partial collection.
- **Only `monthly` / `quarterly`.** A student can legitimately owe several `enrollment` or `other` payments in one month.
- **Cancelling frees the month again**, which is what lets `reconcile_payment_schedule` supersede a stale row with one due in the same month. That command therefore cancels *before* it creates.

`schedule_academic_year_payments` wraps each create in a transaction and swallows the resulting `IntegrityError`: losing the race means the payment now exists, which is the outcome wanted, and one lost race must not abort the remaining periods or the rest of the cron. In the UI the constraint surfaces through `full_clean()` as a Spanish message telling the admin to edit or cancel the existing payment.

Migration `billing/0010` **refuses to apply** against a database that already holds duplicates, naming the offending students, rather than dying with a bare Postgres error part-way through a deploy. If it blocks a deploy, that database really is double-billed: repair it with `manage.py reconcile_payment_schedule` (dry run by default) and re-run.

Cancelled payments count as existing, so a payment an admin soft-deleted through `deactivate_payment` is **not** re-created — by the cron or by `reconcile_payment_schedule`.

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
Both generators short-circuit here, so any queryset feeding them needs `select_related("enrollment_type")`.

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
