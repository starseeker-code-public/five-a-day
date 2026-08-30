"""
Service layer for payment business logic.
Extracted from generate_payments management command and views.
"""

import calendar
from datetime import date
from decimal import Decimal

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
    def calculate_monthly_amount(enrollment, config, month):
        """Calculate the monthly payment amount for a given enrollment."""
        special = PaymentService.hand_priced_amount(enrollment)
        if special is not None:
            return special

        base = PaymentService._get_base_monthly_fee(enrollment, config)
        if enrollment.schedule_type == "adult_group":
            return base

        amount = base

        if enrollment.is_sibling_discount:
            amount -= amount * (config.sibling_discount / Decimal("100"))

        if enrollment.has_language_cheque:
            amount -= config.language_cheque_discount

        if month == 6:
            amount -= config.june_discount

        return max(amount, Decimal("0.01"))

    @staticmethod
    def calculate_quarterly_amount(enrollment, config, quarter_due_month):
        """Calculate a quarterly payment: 3 months, minus every discount the
        enrollment carries.

        This used to apply ONLY the quarterly percentage, so a quarterly
        student with a sibling discount or a language cheque was billed the
        full price — the enrollment record said one number and the generated
        payments said another. The order of operations mirrors
        ``EnrollmentService._apply_discounts`` so the two agree:

            (3 x monthly - quarterly%) - sibling% - (language cheque x 3)

        ``quarter_due_month`` is the month the quarter falls due (10 = Q1
        Oct-Dec, 1 = Q2 Jan-Mar, 4 = Q3 Apr-Jun). Q3 covers June, so it also
        picks up the June "complete the year" discount that
        ``calculate_monthly_amount`` applies to month 6.

        A ``special`` matrícula short-circuits all of it — see
        ``hand_priced_amount``.
        """
        special = PaymentService.hand_priced_amount(enrollment)
        if special is not None:
            return special

        base = PaymentService._get_base_monthly_fee(enrollment, config)
        total = base * 3
        total -= total * (config.quarterly_enrollment_discount / Decimal("100"))

        # Adult groups pay a flat rate — no sibling / cheque / June discounts,
        # matching calculate_monthly_amount.
        if enrollment.schedule_type == "adult_group":
            return max(total, Decimal("0.01"))

        if enrollment.is_sibling_discount:
            total -= total * (config.sibling_discount / Decimal("100"))

        if enrollment.has_language_cheque:
            # The cheque is a per-month amount; a quarter covers three.
            total -= config.language_cheque_discount * 3

        if quarter_due_month == 4:  # Q3 = Apr-Jun, includes the June discount
            total -= config.june_discount

        return max(total, Decimal("0.01"))

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
    def schedule_academic_year_payments(enrollment, parent=None):
        """Create all pending periodic payments for the enrollment's academic year.

        Monthly enrollments get one pending payment per academic month
        (Sep–Jun); quarterly enrollments get three (Q1 due Oct, Q2 Jan, Q3 Apr).
        Each is due at the END of its period and starts at the enrollment month,
        so a student enrolling mid-year is never billed for months before they
        joined.

        Idempotent: skips any (student, payment_type, month, year) that already
        has a payment — so the periodic ``generate_payments`` command (which
        matches on due-date month/year) never double-creates. Returns the number
        of payments created.
        """
        from billing.models import SiteConfiguration

        student = enrollment.student
        if not student.active:
            return 0

        config = SiteConfiguration.get_config()
        start_year = int(enrollment.academic_year.split("-")[0])
        end_year = int(enrollment.academic_year.split("-")[1])
        ref = enrollment.enrollment_date or date.today()

        def cal_year(month):
            return start_year if month >= 9 else end_year

        def period_end(month):
            year = cal_year(month)
            return date(year, month, calendar.monthrange(year, month)[1])

        if enrollment.payment_modality == "quarterly":
            months, payment_type, names = [10, 1, 4], "quarterly", QUARTER_NAMES_ES
        else:
            months, payment_type, names = [9, 10, 11, 12, 1, 2, 3, 4, 5, 6], "monthly", MONTH_NAMES_ES

        created = 0
        for month in months:
            due_date = period_end(month)
            if due_date < ref:
                continue  # period already elapsed at enrollment time

            year = cal_year(month)
            if Payment.objects.filter(
                student=student,
                payment_type=payment_type,
                due_date__month=month,
                due_date__year=year,
            ).exists():
                continue

            if payment_type == "quarterly":
                amount = PaymentService.calculate_quarterly_amount(enrollment, config, month)
                concept = f"Trimestre {names.get(month, '')} {year}"
            else:
                amount = PaymentService.calculate_monthly_amount(enrollment, config, month)
                concept = f"Mensualidad {names.get(month, '')} {year}"

            Payment.objects.create(
                student=student,
                parent=parent,
                enrollment=enrollment,
                payment_type=payment_type,
                payment_method="transfer",
                amount=amount,
                payment_status="pending",
                due_date=due_date,
                concept=concept,
            )
            created += 1

        return created

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
