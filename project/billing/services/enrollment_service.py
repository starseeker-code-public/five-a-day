"""
Service layer for enrollment business logic.
Extracted from EnrollmentForm.create_enrollment() in forms.py.
"""

import logging
from datetime import date
from decimal import Decimal

from django.db import transaction

from billing.models import (
    Enrollment,
    EnrollmentType,
    SiteConfiguration,
    academic_year_end_date,
    academic_year_start_date,
    current_academic_year,
)

logger = logging.getLogger(__name__)


class EnrollmentService:
    @staticmethod
    def create_enrollment(student, enrollment_data, is_adult=False):
        """
        Create and save an Enrollment from validated form data.

        Args:
            student: Student instance
            enrollment_data: dict with keys:
                - enrollment_plan: 'monthly_full' | 'monthly_part' | 'quarterly'
                - has_language_cheque: bool
                - is_sibling_discount: bool
                - is_special: bool
                - manual_amount: Decimal or None
                - is_returning_student: bool (optional) — force the
                  returning-student matrícula category ("Antiguo alumno")
                - start_date: date or None — the day the student STARTS
                  (defaults to today). Drives `enrollment_date`, and with it the
                  academic year and where billing begins: a student signed up
                  today for a 1 November start is billed from November.
            is_adult: bool

        Returns:
            Enrollment instance
        """
        config = SiteConfiguration.get_config()
        start_date = enrollment_data.get("start_date") or date.today()
        academic_year = current_academic_year(start_date)
        start_year = int(academic_year.split("-")[0])
        end_year = int(academic_year.split("-")[1])

        is_special = enrollment_data.get("is_special", False)
        manual_amount = enrollment_data.get("manual_amount")
        has_lc = enrollment_data.get("has_language_cheque", False)
        has_sibling = enrollment_data.get("is_sibling_discount", False)

        base_amount, schedule_type, payment_modality = EnrollmentService._resolve_plan(
            config, enrollment_data, is_adult, is_special, manual_amount
        )
        enrollment_type = EnrollmentService._resolve_enrollment_type(
            student,
            is_adult,
            is_special,
            manual_amount,
            academic_year,
            force_returning=enrollment_data.get("is_returning_student", False),
        )

        discount_pct, final_amount = EnrollmentService._apply_discounts(
            config, base_amount, has_lc, has_sibling, is_adult, payment_modality
        )

        with transaction.atomic():
            enrollment = Enrollment(
                student=student,
                enrollment_type=enrollment_type,
                enrollment_period_start=academic_year_start_date(start_year),
                enrollment_period_end=academic_year_end_date(end_year),
                academic_year=academic_year,
                schedule_type=schedule_type,
                payment_modality=payment_modality,
                has_language_cheque=has_lc,
                is_sibling_discount=has_sibling,
                enrollment_amount=base_amount,
                discount_percentage=discount_pct,
                final_amount=final_amount,
                status="active",
                enrollment_date=start_date,
            )
            enrollment.save()
        return enrollment

    @staticmethod
    def close_active_enrollments(student, status: str) -> int:
        """
        Move every ACTIVE enrollment of `student` to `status`, and return how
        many were moved.

        The one place enrollment status transitions happen, because the obvious
        one-liner is wrong in a way nothing complains about:
        `student.enrollments.filter(status="active").update(status=...)` is a
        single UPDATE that **bypasses the `pre_save`/`post_save` receivers**.
        `Enrollment` is in `core.audit_signals._TRACKED`, so every cancel and
        every finish — moving a student to the waiting list, superseding a
        matrícula on a plan change, re-enrolling — wrote NO `AuditLog` row, and
        did not bump `updated_at` either (`auto_now` only fires on `save()`).
        The audit trail recorded enrollments being created and never recorded
        one being cancelled, which is exactly the half you would go looking for.

        Saving row by row costs one UPDATE per active enrollment instead of one
        for all of them. The `unique_active_enrollment_per_student` constraint
        caps that at one row per student, so in practice it is the same query
        either way.
        """
        closed = 0
        for enrollment in student.enrollments.filter(status="active"):
            enrollment.status = status
            enrollment.save(update_fields=["status", "updated_at"])
            closed += 1
        return closed

    @staticmethod
    def is_returning_student(student, this_academic_year: str | None = None) -> bool:
        """
        A "returning student" is one who has at least one prior Enrollment
        in a *different* (earlier) academic year.

          - Empty enrollments → new student, False
          - Every enrollment is for `this_academic_year` (i.e. this signup) → False
          - Any enrollment for a prior year → True

        `this_academic_year` defaults to the current academic year.
        """
        if this_academic_year is None:
            this_academic_year = current_academic_year()
        return student.enrollments.exclude(academic_year=this_academic_year).exists()

    @staticmethod
    def compute_enrollment_fee(
        config, student, is_adult: bool, special_fee=None, force_returning=False, this_academic_year=None
    ) -> tuple:
        """
        Return `(final_fee, returning_discount_applied)` — the enrollment fee
        for this student, minus the returning-student discount when the
        student re-enrols in a later academic year.

        `this_academic_year` is the year of the enrollment being charged
        (defaults to the current signup year). Callers that just created the
        enrollment must pass `enrollment.academic_year`: with a start date the
        enrollment's year can differ from today's, and judged against today's
        year the student's own brand-new enrollment would read as prior
        history and wrongly grant the discount.

        `force_returning` is the form's "Antiguo alumno" checkbox: the admin
        vouching that this is a returning student even though the Student row
        has no prior Enrollment (a fresh row for someone who studied here
        before, e.g. promoted off the waiting list or re-registered after
        years away). It grants the discount; it never revokes one the prior
        enrollments already earn.

        Adults are NOT eligible for the returning-student discount (they
        already have `adult_enrollment_fee` which is a separate rate).

        `special_fee` is the optional hand-set matrícula (the form's
        "Matrícula especial (€)"). It is a NEGOTIATED figure, so it is returned
        verbatim: no returning-student discount is taken off a price that was
        already agreed with the family. It is deliberately separate from the
        enrollment's `manual_amount`, which prices the recurring fee only — a
        special monthly price does not imply a special matrícula, and before
        v1.17.5 a special enrollment was silently charged the standard one.
        """
        if special_fee:
            return Decimal(special_fee), Decimal("0.00")

        base = config.adult_enrollment_fee if is_adult else config.children_enrollment_fee
        if is_adult:
            return base, Decimal("0.00")

        discount = getattr(config, "returning_student_enrollment_discount", Decimal("0.00")) or Decimal("0.00")
        if discount <= 0:
            return base, Decimal("0.00")

        if not (force_returning or EnrollmentService.is_returning_student(student, this_academic_year)):
            return base, Decimal("0.00")

        final = max(base - discount, Decimal("0.00"))
        return final, discount

    @staticmethod
    def _resolve_enrollment_type(
        student, is_adult, is_special, manual_amount, academic_year=None, force_returning=False
    ):
        """
        Pick the matrícula category this enrollment belongs to.

        The four categories are mutually exclusive and ordered by precedence: a
        hand-priced enrollment is `special` whoever it is for, an adult pays the adult
        matrícula, and a child is either re-enrolling (`returning_student`, the
        discounted matrícula) or signing up for the first time (`new_student`).
        `force_returning` (the "Antiguo alumno" checkbox) marks a child as
        re-enrolling even when no prior Enrollment row exists; it does not
        outrank `special` or `adults`.

        This is deliberately independent of `enrollment_plan` — monthly vs quarterly is
        `payment_modality`, not a kind of matrícula.
        """

        def _get_type(name):
            try:
                return EnrollmentType.objects.get(name=name)
            except EnrollmentType.DoesNotExist as err:
                raise ValueError(f"EnrollmentType '{name}' not found. Run seed data or create it in admin.") from err

        if is_special and manual_amount:
            return _get_type("special")
        if is_adult:
            return _get_type("adults")
        if force_returning or EnrollmentService.is_returning_student(student, academic_year):
            return _get_type("returning_student")
        return _get_type("new_student")

    @staticmethod
    def _resolve_plan(config, data, is_adult, is_special, manual_amount):
        """
        Determine the recurring period fee and how it is scheduled.
        Returns: (base_amount, schedule_type, payment_modality)
        """
        if is_adult:
            base = manual_amount if (is_special and manual_amount) else config.adult_group_monthly_fee
            return base, "adult_group", "monthly"

        plan = data.get("enrollment_plan", "monthly_full")

        if is_special and manual_amount:
            if plan == "monthly_part":
                return manual_amount, "part_time", "monthly"
            elif plan == "quarterly":
                return manual_amount, "full_time", "quarterly"
            return manual_amount, "full_time", "monthly"

        if plan == "monthly_part":
            return config.part_time_monthly_fee, "part_time", "monthly"
        elif plan == "quarterly":
            quarterly_base = config.full_time_monthly_fee * 3
            quarterly_discount = config.quarterly_enrollment_discount
            base_amount = quarterly_base * (1 - quarterly_discount / Decimal("100"))
            return base_amount, "full_time", "quarterly"
        return config.full_time_monthly_fee, "full_time", "monthly"

    @staticmethod
    def _apply_discounts(config, base_amount, has_lc, has_sibling, is_adult, payment_modality):
        """
        Apply discounts and return (discount_pct, final_amount).
        """
        discount_pct = Decimal("0")
        final_amount = base_amount

        if has_sibling and not is_adult:
            discount_pct += config.sibling_discount
            final_amount = base_amount * (1 - config.sibling_discount / Decimal("100"))

        if has_lc and not is_adult:
            lc_amount = config.language_cheque_discount
            if payment_modality == "quarterly":
                lc_amount = lc_amount * 3
            final_amount = final_amount - lc_amount

        if final_amount < Decimal("0.01"):
            final_amount = Decimal("0.01")

        return discount_pct, final_amount
