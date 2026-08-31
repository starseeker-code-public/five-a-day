"""
Service layer for payment business logic.
Extracted from generate_payments management command and views.
"""

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from billing.models import Payment

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

QUARTER_NAMES_ES = {
    10: "1er Trimestre (Oct-Dic)",
    1: "2do Trimestre (Ene-Mar)",
    4: "3er Trimestre (Abr-Jun)",
}


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

        special = PaymentService.hand_priced_amount(enrollment)
        if special is not None:
            whole_period = Decimal(3) if quarterly else Decimal(1)
            return max(special * (effective / whole_period), Decimal("0.01"))

        base = PaymentService._get_base_monthly_fee(enrollment, config)
        total = base * effective

        if quarterly:
            total -= total * (config.quarterly_enrollment_discount / Decimal("100"))

        # Adult groups pay a flat rate — no sibling / cheque / June discounts.
        if enrollment.schedule_type == "adult_group":
            return max(total, Decimal("0.01"))

        if enrollment.is_sibling_discount:
            total -= total * (config.sibling_discount / Decimal("100"))

        if enrollment.has_language_cheque:
            # The cheque is a per-month amount; a period covers `effective` of them.
            total -= config.language_cheque_discount * effective

        if 6 in months:  # June carries the "complete the year" discount
            total -= config.june_discount

        return max(total, Decimal("0.01"))

    @staticmethod
    def calculate_monthly_amount(enrollment, config, month, fraction=Decimal("1")):
        """Price one monthly fee. Thin wrapper over ``calculate_period_amount``."""
        return PaymentService.calculate_period_amount(enrollment, config, [month], fraction, quarterly=False)

    @staticmethod
    def calculate_quarterly_amount(enrollment, config, quarter_due_month, fraction=Decimal("1")):
        """Price a standard 3-month quarter. Thin wrapper over ``calculate_period_amount``.

        ``quarter_due_month`` names the quarter by its first month under the old
        FIXED calendar (10 = Oct-Dec, 1 = Jan-Mar, 4 = Apr-Jun). Since v1.22 the
        generator anchors quarters to the enrollment month instead, so this helper
        is kept for the standard-price question "what does a full quarter cost?" —
        the reminder email and the pricing preview still ask exactly that.
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
    def complete_payment(payment_id):
        """Mark a payment as completed. Returns the updated Payment."""
        with transaction.atomic():
            payment = Payment.objects.select_related("student").get(id=payment_id)
            payment.payment_status = "completed"
            payment.payment_date = date.today()
            payment.save()
        return payment

    @staticmethod
    def schedule_academic_year_payments(enrollment, parent=None, as_of=None):
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
        """
        from billing.models import SiteConfiguration

        student = enrollment.student
        if not student.active:
            return 0

        today = as_of or date.today()
        config = SiteConfiguration.get_config()
        quarterly = enrollment.payment_modality == "quarterly"
        payment_type = "quarterly" if quarterly else "monthly"

        created = 0
        for index, period in enumerate(PaymentService.billing_periods(enrollment)):
            # The FIRST period is always issued, even when it opens in the future:
            # enrolling a student in August for a course starting in September must
            # still put their first fee on the ficha that day. Every later period
            # waits for its own first day, when Celery creates it.
            if index > 0 and period["starts"] > today:
                break

            due = period["due"]
            if Payment.objects.filter(
                student=student,
                payment_type=payment_type,
                due_date__month=due.month,
                due_date__year=due.year,
            ).exists():
                continue

            amount = PaymentService.calculate_period_amount(
                enrollment, config, [m for m, _ in period["months"]], period["fraction"], quarterly
            )

            Payment.objects.create(
                student=student,
                parent=parent,
                enrollment=enrollment,
                payment_type=payment_type,
                payment_method="transfer",
                amount=amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                payment_status="pending",
                due_date=due,
                concept=PaymentService.period_concept(period, quarterly),
            )
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

    @staticmethod
    def should_generate_monthly(month):
        """Monthly payments are generated for Sep through Jun."""
        return month in range(1, 7) or month in range(9, 13)

    @staticmethod
    def should_generate_quarterly(month):
        """Quarterly payments are generated in Oct (Q1), Jan (Q2), Apr (Q3)."""
        return month in (10, 1, 4)

    @staticmethod
    def get_payment_statistics(month, year):
        """Calculate payment statistics for a given month/year."""
        from django.db.models import Sum

        pending = Payment.objects.filter(
            payment_status="pending",
            due_date__month=month,
            due_date__year=year,
        )
        completed = Payment.objects.filter(
            payment_status="completed",
            payment_date__month=month,
            payment_date__year=year,
        )

        pending_total = pending.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        completed_total = completed.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

        return {
            "pending_count": pending.count(),
            "pending_total": pending_total,
            "completed_count": completed.count(),
            "completed_total": completed_total,
            "expected_total": pending_total + completed_total,
        }
