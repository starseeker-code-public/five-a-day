"""
Service layer for payment business logic.
Extracted from generate_payments management command and views.
"""

import calendar
import logging
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models.functions import ExtractMonth, ExtractYear

from billing.constants import PERIODIC_PAYMENT_TYPES
from billing.models import Payment

logger = logging.getLogger(__name__)

MONTH_NAMES_ES = {
    9: "Septiembre",
    10: "Octubre",
    11: "Noviembre",
    12: "Diciembre",
    1: "Enero",
    2: "Febrero",
    3: "Marzo",
    4: "Abril",
    5: "Mayo",
    6: "Junio",
}

TEACHING_MONTHS = [9, 10, 11, 12, 1, 2, 3, 4, 5, 6]


class PaymentService:
    @staticmethod
    def _get_base_monthly_fee(enrollment, config):
        """Get base monthly fee by schedule type."""
        if enrollment.schedule_type == "adult_group":
            return config.adult_group_monthly_fee
        elif enrollment.schedule_type == "full_time":
            return config.full_time_monthly_fee
        return config.part_time_monthly_fee

    @staticmethod
    def hand_priced_amount(enrollment):
        """The agreed period fee of a `special` matrícula, or None if there isn't one.

        A special enrollment is priced BY HAND: the admin types the amount, and
        ``EnrollmentService._resolve_plan`` stores it on the enrollment itself
        (already carrying whatever sibling / cheque discount was ticked — see
        ``_apply_discounts``). Its ``schedule_type`` is then just the timetable the
        student happens to attend, NOT a price band.

        Both generators used to re-derive the fee from SiteConfiguration via
        ``_get_base_monthly_fee``, so a hand-priced student was billed the standard
        1-day / 2-day rate every month while the enrollment record showed the custom
        one. ``final_amount`` is the per-PERIOD figure in both modalities (for
        quarterly specials the admin types the price of the whole quarter), so it is
        used as-is: no further config discount is layered on top of a negotiated price.
        """
        enrollment_type = enrollment.enrollment_type
        if enrollment_type is not None and enrollment_type.name == "special":
            return max(enrollment.final_amount, Decimal("0.01"))
        return None

    @staticmethod
    def calculate_period_amount(enrollment, config, months, fraction=Decimal("1"), quarterly=False):
        """Price one billing period.

        ``months`` is the list of teaching months the period covers (``[12]`` for
        a monthly December, ``[12, 1, 2]`` for a quarter starting in December).
        ``fraction`` prorates the period's FIRST month for a student who joined
        part-way through it — 15 September on a 30-day month gives 16/30. Every
        later month of the period, and every later period, is billed in full.

        This is the single source of truth for what a period costs;
        ``calculate_monthly_amount`` and ``calculate_quarterly_amount`` are thin
        wrappers over it so the standard-price helpers and the generator can
        never drift apart. The order of operations mirrors
        ``EnrollmentService._apply_discounts``:

            (months x monthly - quarterly%) - sibling% - (cheque x months) - june

        A ``special`` matricula short-circuits all of it — see
        ``hand_priced_amount`` — but is still scaled when the period is short or
        partial, because ``final_amount`` is the price of a WHOLE period.
        """
        # Effective months billed: full months, plus the prorated first one.
        effective = Decimal(len(months) - 1) + fraction
        return PaymentService._price_months(enrollment, config, months, effective, quarterly)

    @staticmethod
    def _price_months(enrollment, config, months, effective, quarterly):
        """Price ``effective`` months' worth of the teaching months ``months``.

        Split out of ``calculate_period_amount`` so a period that is short at
        BOTH ends can be priced by the same code. ``calculate_period_amount``
        expresses "the first month is partial" (one fraction, on month 0), which
        is all the schedule generator ever needs; ``close_out_periods`` bills a
        block truncated by a plan change and has to hand in the total directly.
        Any new amount calculation must come through here — a second copy of this
        discount ORDER is a family charged one figure and quoted another.
        """
        special = PaymentService.hand_priced_amount(enrollment)
        if special is not None:
            whole_period = Decimal(3) if quarterly else Decimal(1)
            return PaymentService._round_money(special * (effective / whole_period))

        base = PaymentService._get_base_monthly_fee(enrollment, config)
        total = base * effective

        if quarterly:
            total -= total * (config.quarterly_enrollment_discount / Decimal("100"))

        # Adult groups pay a flat rate — no sibling / cheque / June discounts.
        if enrollment.schedule_type == "adult_group":
            return PaymentService._round_money(total)

        if enrollment.is_sibling_discount:
            total -= total * (config.sibling_discount / Decimal("100"))

        if enrollment.has_language_cheque:
            # The cheque is a per-month amount; a period covers `effective` of them.
            total -= config.language_cheque_discount * effective

        if 6 in months:  # June carries the "complete the year" discount
            total -= config.june_discount

        return PaymentService._round_money(total)

    @staticmethod
    def _round_money(value):
        """The ONE rounding for a period price — floor €0.01, quantize HALF_UP.

        Delegates to `billing.services.pricing_service.round_money`, which is
        where the rule now lives because `Enrollment.save()` needs it too and
        cannot import this module (it is imported BY this module). This method
        stays as the name every billing caller already reaches for; see the
        helper for why the floor and the HALF_UP matter.
        """
        from billing.services.pricing_service import round_money

        return round_money(value)

    @staticmethod
    def calculate_monthly_amount(enrollment, config, month, fraction=Decimal("1")):
        """Price one monthly fee. Thin wrapper over ``calculate_period_amount``."""
        return PaymentService.calculate_period_amount(enrollment, config, [month], fraction, quarterly=False)

    @staticmethod
    def calculate_quarterly_amount(enrollment, config, quarter_due_month, fraction=Decimal("1")):
        """Price a standard 3-month quarter. Thin wrapper over ``calculate_period_amount``.

        ``quarter_due_month`` names the quarter by its first month under the old
        FIXED calendar (10 = Oct-Dec, 1 = Jan-Mar, 4 = Apr-Jun). Since v1.22.0 the
        generator anchors quarters to the enrollment month instead, so this helper
        no longer describes anything the generator does: it answers the separate
        standard-price question "what does a full quarter cost for this
        enrollment?".

        It needs an Enrollment, so the two callers that ask that question WITHOUT
        one — the payment-reminder email and the pricing preview — cannot use it
        and derive the same figures in ``PricingService``. That duplication is
        pinned by ``tests/unit/test_pricing_matches_billing.py``; if you change
        the discount order here, that test tells you the advertised prices moved
        too.
        """
        months = [((quarter_due_month - 1 + i) % 12) + 1 for i in range(3)]
        return PaymentService.calculate_period_amount(enrollment, config, months, fraction, quarterly=True)

    @staticmethod
    def teaching_months(academic_year):
        """[(month, year), ...] for Sep..Jun of ``academic_year`` ("2025-2026")."""
        start_year, end_year = (int(part) for part in academic_year.split("-"))
        return [(m, start_year if m >= 9 else end_year) for m in TEACHING_MONTHS]

    @staticmethod
    def _last_day(month, year):
        return date(year, month, calendar.monthrange(year, month)[1])

    @staticmethod
    def proration_fraction(reference, month, year):
        """Fraction of (month, year) still to be taught from ``reference`` onwards.

        A student joining on the 15th of a 30-day month is billed 16/30 — the day
        they join counts. Joining on or before the 1st (or in an earlier month)
        bills the whole thing.
        """
        days_in_month = calendar.monthrange(year, month)[1]
        if (reference.year, reference.month) != (year, month) or reference.day <= 1:
            return Decimal("1")
        remaining = days_in_month - reference.day + 1
        return Decimal(remaining) / Decimal(days_in_month)

    @staticmethod
    def covered_months(student):
        """`{(month, year)}` a periodic payment of `student` already invoices. One query.

        BOTH cadences are read, and that is the whole point. The generators' own
        idempotency (`billed_months_map`, `pending_periods`) is keyed on
        `(student, payment_type, due month)`, so it is structurally blind across
        a cadence change — which is how flipping a student from monthly to
        quarterly re-billed months already COLLECTED under the old cadence, at
        full price, with `unique_pending_periodic_payment_per_month` unable to
        help (it is pending-only AND keyed on `payment_type`).

        A `monthly` row covers its due month. A `quarterly` row covers its due
        month and the two before it — the block it was priced for, since quarters
        are anchored to the enrollment month and fall due on the last day of
        their third month.

        Cancelled rows are excluded, deliberately and unlike `billed_months_map`:
        cancelling frees the month under the DB constraint, so a superseded row
        must not keep a month reserved forever (a cancelled row holding its month
        is exactly what made a mis-billed transition unrepairable). Everything
        else counts, `pending` included — an invoice already sent to a family is
        not re-issued under a different plan.

        The one-month June stub reports the two months before June as covered as
        well, because a row cannot say whether its block was short. That can only
        push a plan change LATER, never re-bill a month that was paid, which is
        the direction that cannot cost a family money.
        """
        covered: set[tuple[int, int]] = set()
        rows = (
            Payment.objects.filter(student=student, payment_type__in=PERIODIC_PAYMENT_TYPES)
            .exclude(payment_status="cancelled")
            .exclude(due_date__isnull=True)
            .values_list("payment_type", "due_date")
        )
        for payment_type, due in rows:
            month, year = due.month, due.year
            for _ in range(3 if payment_type == "quarterly" else 1):
                covered.add((month, year))
                month -= 1
                if month == 0:
                    month, year = 12, year - 1
        return covered

    @staticmethod
    def transition_start_date(student, requested_start=None, closing=None):
        """The date a REPLACEMENT enrollment may start billing from, or None.

        The single answer to "when does a plan change take effect?", used by
        `EnrollmentService.supersede_enrollment` for all three transitions (a
        modality flip, an edited plan, a new matrícula for an existing student).
        Two rules, and both exist because a month can only be billed once:

        1. **Never inside a month another period already invoices.** The first
           candidate month not in `covered_months()` wins, so the new cadence's
           `billing_periods` cannot reach back over money already collected or
           already invoiced.
        2. **Never mid-month while the closing enrollment is still teaching.**
           When `closing` taught the first part of `requested_start`'s month, the
           handover is moved to the 1st of the FOLLOWING month and the closing
           plan keeps that month whole.

        Rule 2 is the judgement call, and the database makes it for us: splitting
        the transition month would mean two periodic rows falling due in it —
        the closing enrollment's head days and the replacement's prorated tail —
        and `unique_pending_periodic_payment_per_month` allows exactly ONE pending
        periodic row per student per due month. The alternatives were the two bugs
        this replaces: bill both (a month charged twice) or bill neither head
        (days taught for free, permanently, because a cancelled row still occupies
        its month for the cron's back-fill). Keeping the month whole on the old
        plan bills it exactly once, at a price the family was already quoted, and
        the endpoints report the effective date so nothing is silent.

        A `requested_start` on the 1st is honoured as-is, and so is one for a
        month the closing enrollment does not teach (a re-enrollment for the next
        course, or a backdated start before the closing enrollment began) — there
        is no split to avoid, so proration is left intact.

        Returns None when every remaining month of the course is already
        invoiced: there is nothing left to re-bill, so the caller must refuse the
        change rather than issue an enrollment that can never generate a payment.
        """
        from billing.models import enrollment_academic_year

        requested_start = requested_start or date.today()
        earliest = requested_start

        if closing is not None and requested_start.day > 1 and closing.enrollment_date is not None:
            teaches_transition_month = (requested_start.month, requested_start.year) in set(
                PaymentService.teaching_months(closing.academic_year)
            )
            if teaches_transition_month and closing.enrollment_date < requested_start:
                earliest = PaymentService._first_day_of_next_month(requested_start)

        covered = PaymentService.covered_months(student)
        academic_year = enrollment_academic_year(requested_start)
        for month, year in PaymentService.teaching_months(academic_year):
            if PaymentService._last_day(month, year) < earliest:
                continue
            if (month, year) in covered:
                continue
            # `max` keeps a mid-month request in an uncovered month exactly where
            # the admin put it, so its first period stays prorated; only a month
            # we had to skip forward to starts on the 1st.
            return max(date(year, month, 1), earliest)
        return None

    @staticmethod
    def _first_day_of_next_month(reference):
        if reference.month == 12:
            return date(reference.year + 1, 1, 1)
        return date(reference.year, reference.month + 1, 1)

    @staticmethod
    def billing_periods(enrollment):
        """Ordered billing periods for the enrollment, from its start month to June.

        Monthly plans get one period per teaching month. Quarterly plans get
        consecutive 3-month blocks ANCHORED TO THE ENROLLMENT MONTH — not the old
        fixed Oct/Jan/Apr calendar, which silently left a mid-year joiner's first
        months unbilled (a student enrolling 12 Dec was never charged for December,
        because Q1 had already fallen due on 31 Oct). The final block is short when
        the academic year ends first: enrolling in December gives Dec-Feb, Mar-May
        and a one-month June.

        Each period is a dict:
            months   [(month, year), ...] the period covers
            starts   date the period opens — when Celery creates the payment
            due      LAST day of the period — when the money is owed
            fraction proration applied to the period's first month (1 unless the
                     student joined part-way through it)
        """
        sequence = PaymentService.teaching_months(enrollment.academic_year)
        reference = enrollment.enrollment_date or date.today()

        # First period is the one still open when the student joined; anything
        # whose last day already passed is not billable to them.
        first_index = next(
            (i for i, (m, y) in enumerate(sequence) if PaymentService._last_day(m, y) >= reference),
            None,
        )
        if first_index is None:
            return []

        remaining = sequence[first_index:]
        step = 3 if enrollment.payment_modality == "quarterly" else 1

        periods = []
        for offset in range(0, len(remaining), step):
            block = remaining[offset : offset + step]
            first_month, first_year = block[0]
            last_month, last_year = block[-1]
            periods.append(
                {
                    "months": block,
                    "starts": max(date(first_year, first_month, 1), reference),
                    "due": PaymentService._last_day(last_month, last_year),
                    "fraction": (
                        PaymentService.proration_fraction(reference, first_month, first_year)
                        if offset == 0
                        else Decimal("1")
                    ),
                }
            )
        return periods

    @staticmethod
    def billed_months_map(student_ids):
        """`{(student_id, payment_type): {(month, year), ...}}` in ONE query.

        `pending_periods` resolves one enrollment's billed months with its own
        query, which is correct but is called in a loop over EVERY active
        enrollment by both `generate_payments` and `reconcile_payment_schedule`
        — exactly one `SELECT payments` per enrollment, so ~2,000 round trips per
        monthly run at the roll this academy is sized for. Passing the result of
        this into `pending_periods` collapses that to one.

        Keyed on `(student_id, payment_type)` and not on the enrollment, because
        that is what the per-enrollment query matches on: a student's periodic
        payments occupy their due month regardless of which enrollment row
        issued them. Every status is included, cancelled ones too — a payment an
        admin soft-deleted must not be silently re-created.
        """
        billed: dict[tuple[int, str], set[tuple[int, int]]] = {}
        rows = (
            Payment.objects.filter(student_id__in=list(student_ids), payment_type__in=PERIODIC_PAYMENT_TYPES)
            .exclude(due_date__isnull=True)
            .annotate(_m=ExtractMonth("due_date"), _y=ExtractYear("due_date"))
            .values_list("student_id", "payment_type", "_m", "_y")
        )
        for student_id, payment_type, month, year in rows:
            billed.setdefault((student_id, payment_type), set()).add((month, year))
        return billed

    @staticmethod
    def pending_periods(enrollment, as_of=None, billed_months=None):
        """The billing periods that are due to be created for ``enrollment`` right now.

        This is the single decision point for "should this period be billed
        yet?", and both callers go through it: the real generator below and
        ``generate_payments --dry-run``. They used to apply the rules
        separately and had already drifted apart on both of them — the preview
        skipped a first period opening in the future (the real run always
        issues it) and keyed idempotency on ``enrollment`` + the exact due
        date instead of ``payment_type`` + the due month/year, so a preview
        could disagree with the run it was previewing.

        Two rules, in order:

        * The FIRST period is always offered, even when it opens in the future
          — enrolling a student in August must put their September fee on the
          ficha that day. Every later period waits for its own first day, and
          because periods are ordered we can stop at the first one that has
          not started.
        * A period already carrying a payment of this type, due in the same
          month, is skipped. Matching on month/year rather than the exact date
          is what makes the generator and this call idempotent against each
          other. Cancelled rows still count as existing, so a payment an admin
          soft-deleted is not silently re-created.
        """
        today = as_of or date.today()
        quarterly = enrollment.payment_modality == "quarterly"
        payment_type = "quarterly" if quarterly else "monthly"

        periods = PaymentService.billing_periods(enrollment)
        if not periods:
            return []

        # One query for the whole schedule instead of an `.exists()` per period.
        # The old loop cost up to 10 round trips per enrollment, and the cron
        # walks every active enrollment in the academy.
        #
        # A caller iterating many enrollments can hand in the set it already
        # resolved (see `billed_months_map`) and skip this query entirely — which
        # is the difference between one query and one per enrollment across the
        # whole roll. `None` and an empty set are NOT the same thing: a student
        # with nothing billed yet has an empty set, so test for None.
        if billed_months is None:
            billed_months = {
                (d.month, d.year)
                for d in Payment.objects.filter(
                    student=enrollment.student,
                    payment_type=payment_type,
                ).values_list("due_date", flat=True)
                if d is not None
            }

        due_for_type = []
        for index, period in enumerate(periods):
            if index > 0 and period["starts"] > today:
                break

            due = period["due"]
            if (due.month, due.year) in billed_months:
                continue

            due_for_type.append(period)
        return due_for_type

    @staticmethod
    def schedule_academic_year_payments(enrollment, parent=None, as_of=None, billed_months=None):
        """Create every periodic payment whose period has STARTED, and no more.

        Payments are created on the first day of their period and fall due on its
        LAST day. So enrolling a student creates just the period they joined
        (prorated for the days already gone), and the 1st-of-the-month Celery job
        adds each new one as it opens. The first period is always issued even if
        it starts later, so a student enrolled in August still has their September
        fee on the ficha immediately.

        The job is also self-healing: it creates any period that has started but
        has no payment, so a month the cron missed is picked up on the next run
        rather than going silently unbilled forever.

        Idempotent — skips any (student, payment_type, due month/year) that already
        exists, which is what stops the cron and this call from double-creating.
        Returns the number of payments created.

        `billed_months` is the already-resolved set for THIS student and payment
        type (see `billed_months_map`); a caller looping over the whole roll passes
        it so this does not spend a query per enrollment. It is MUTATED as rows are
        created, so a second call for the same student in the same run sees what the
        first one issued — without that the batched path would be less idempotent
        than the unbatched one, which is the trap in caching a "what exists" set
        across writes.
        """
        from billing.models import SiteConfiguration

        student = enrollment.student
        if not student.active:
            return 0

        today = as_of or date.today()
        config = SiteConfiguration.get_config()
        quarterly = enrollment.payment_modality == "quarterly"
        payment_type = "quarterly" if quarterly else "monthly"

        # An active enrollment with NO billable periods at all is a data problem,
        # not a normal state — it means `enrollment_date` falls outside
        # `academic_year`'s teaching window (e.g. the year was edited to a past
        # course in /admin/). Every caller treats "0 created" as success, and the
        # cron cannot back-fill because it goes through this same helper, so say
        # so loudly instead of returning 0 in silence.
        if not PaymentService.billing_periods(enrollment):
            logger.warning(
                "Enrollment %d (student %d) has no billable periods: enrollment_date %s "
                "falls outside academic year %s. No payments will ever be generated for it.",
                int(enrollment.pk),
                int(enrollment.student_id),
                enrollment.enrollment_date.isoformat() if enrollment.enrollment_date else "None",
                enrollment.academic_year,
            )
            return 0

        created = 0
        for period in PaymentService.pending_periods(enrollment, as_of=today, billed_months=billed_months):
            amount = PaymentService.calculate_period_amount(
                enrollment, config, [m for m, _ in period["months"]], period["fraction"], quarterly
            )

            # `pending_periods` already decided this period is unbilled, but that is
            # a read-then-write: `payments.unique_pending_periodic_payment_per_month`
            # is what actually guarantees it, and a concurrent run (Cloud Run Jobs retry
            # on failure) can lose the race. Losing it means the payment now exists,
            # which is the outcome we wanted — so swallow it and carry on rather than
            # aborting the periods that follow. `atomic()` per create because an
            # IntegrityError otherwise poisons the surrounding transaction.
            try:
                with transaction.atomic():
                    Payment.objects.create(
                        student=student,
                        parent=parent,
                        enrollment=enrollment,
                        payment_type=payment_type,
                        payment_method="transfer",
                        amount=amount,  # already quantized by calculate_period_amount
                        payment_status="pending",
                        due_date=period["due"],
                        concept=PaymentService.period_concept(period, quarterly),
                    )
            except IntegrityError:
                logger.warning(
                    "Periodic payment for student %d due %s already existed; skipped.",
                    int(student.pk),
                    period["due"].isoformat(),
                )
                if billed_months is not None:
                    billed_months.add((period["due"].month, period["due"].year))
                continue
            if billed_months is not None:
                billed_months.add((period["due"].month, period["due"].year))
            created += 1

        return created

    @staticmethod
    def close_out_periods(enrollment, parent=None, until=None):
        """Bill every period `enrollment` taught IN FULL before `until`, and nothing else.

        The closing half of a plan change (`EnrollmentService.supersede_enrollment`).
        `generate_payments` only visits ACTIVE enrollments, so a month the closing
        enrollment taught and never billed becomes structurally unbillable the
        instant it is finished — no back-fill can reach it, because the
        replacement's schedule starts later. This issues those months at the
        closing enrollment's own price, before it is closed.

        Deliberately NOT `schedule_academic_year_payments(..., as_of=until - 1 day)`,
        which is what the view helper this replaces did. That helper always issues
        an enrollment's FIRST period even when it opens in the future (so an
        August signup sees their September fee immediately) — here that meant
        issuing, at the old price and in full, exactly the period the replacement
        was about to issue prorated. The family was then billed the old whole
        month and the new prorated one was silently dropped by the billed-month
        check.

        `until` is a month boundary for every transition
        `EnrollmentService.supersede_enrollment` allows, so a MONTHLY plan's
        periods never straddle it. A quarterly block can, and it is billed for
        its taught months only, due on the last of them — a short quarter, priced
        by the same `_price_months` the one-month June stub uses.

        Skipping is deliberately checked TWICE, and the second check is the one
        that matters here. Same-cadence idempotency (`billed_months`, cancelled
        rows included, exactly as the generators do it) stops a re-run and
        respects a payment an admin deliberately cancelled. But this method runs
        at a moment when the student may carry rows of the OTHER cadence — that
        is the whole reason the transition exists — and those are invisible to a
        `payment_type`-keyed check, which is the same blindness that let a
        modality flip double-bill in the first place. So a period is also skipped
        when `covered_months()` already accounts for ANY of its months. That is
        conservative for a quarter (one overlapping month skips the whole block,
        leaving the other two for `reconcile_payment_schedule`), and conservative
        is the right direction: under-billing is repairable, a family charged
        twice is not.

        Returns the number of payments created.
        """
        from billing.models import SiteConfiguration

        student = enrollment.student
        if not student.active or until is None:
            return 0

        config = SiteConfiguration.get_config()
        quarterly = enrollment.payment_modality == "quarterly"
        payment_type = "quarterly" if quarterly else "monthly"

        billed_months = {
            (d.month, d.year)
            for d in Payment.objects.filter(student=student, payment_type=payment_type).values_list(
                "due_date", flat=True
            )
            if d is not None
        }
        covered = PaymentService.covered_months(student)

        created = 0
        for period in PaymentService.billing_periods(enrollment):
            # Months whose LAST day is before the handover: taught in full by this
            # enrollment. Anything else belongs to the replacement.
            months = [(m, y) for (m, y) in period["months"] if PaymentService._last_day(m, y) < until]
            if not months:
                # Periods are ordered, so nothing later can qualify either.
                break

            # The period's own join proration always applies to its FIRST month
            # and truncation only ever drops months off the END, so the fraction
            # carries over unchanged.
            fraction = period["fraction"]
            effective = Decimal(len(months) - 1) + fraction
            due = PaymentService._last_day(months[-1][0], months[-1][1])
            if (due.month, due.year) in billed_months:
                continue
            if any(month in covered for month in months):
                continue

            amount = PaymentService._price_months(enrollment, config, [m for m, _ in months], effective, quarterly)
            truncated = {"months": months, "fraction": fraction}
            try:
                with transaction.atomic():
                    Payment.objects.create(
                        student=student,
                        parent=parent,
                        enrollment=enrollment,
                        payment_type=payment_type,
                        payment_method="transfer",
                        amount=amount,
                        payment_status="pending",
                        due_date=due,
                        concept=PaymentService.period_concept(truncated, quarterly),
                    )
            except IntegrityError:
                logger.warning(
                    "Close-out payment for student %d due %s already existed; skipped.",
                    int(student.pk),
                    due.isoformat(),
                )
                billed_months.add((due.month, due.year))
                continue
            billed_months.add((due.month, due.year))
            created += 1

        return created

    @staticmethod
    def period_concept(period, quarterly):
        """Human label for a period: "Mensualidad Diciembre 2025" / "Trimestre
        Diciembre-Febrero 2026". Quarters are anchored to the enrollment month, so
        the old fixed QUARTER_NAMES_ES labels ("2do Trimestre (Ene-Mar)") no longer
        describe them and the range is spelled out instead. A partial first month
        is marked so the family can see why the amount is not the usual one.
        """
        months = period["months"]
        first_month, _ = months[0]
        last_month, last_year = months[-1]
        partial = " (parcial)" if period["fraction"] != Decimal("1") else ""

        if not quarterly:
            return f"Mensualidad {MONTH_NAMES_ES.get(first_month, '')} {last_year}{partial}"
        if len(months) == 1:
            return f"Trimestre {MONTH_NAMES_ES.get(first_month, '')} {last_year}{partial}"
        return (
            f"Trimestre {MONTH_NAMES_ES.get(first_month, '')}-{MONTH_NAMES_ES.get(last_month, '')} {last_year}{partial}"
        )
