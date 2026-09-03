"""
Service layer for enrollment business logic.
Extracted from EnrollmentForm.create_enrollment() in forms.py.
"""

import logging
from datetime import date
from decimal import Decimal

from django.db import transaction

from billing.constants import PERIODIC_PAYMENT_TYPES
from billing.models import (
    Enrollment,
    EnrollmentType,
    SiteConfiguration,
    academic_year_end_date,
    academic_year_start_date,
    current_academic_year,
    enrollment_academic_year,
)
from billing.services.pricing_service import (
    period_base_amount,
    quarterly_price_from_monthly,
    round_money,
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
        # NOT `current_academic_year`: its May rollover stamped a 15 May starter
        # with the NEXT course, whose teaching months begin in September — so the
        # May and June they actually attend were structurally unbillable and the
        # enrollment was invisible to the May-August cron runs. See
        # `enrollment_academic_year` for the full rule (Sep-Jun joins the running
        # course, Jul/Aug joins the one starting in September).
        academic_year = enrollment_academic_year(start_date)
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
    def close_active_enrollments(
        student,
        status: str,
        cancel_pending_periodic: bool = False,
        keep_payment_type: str | None = None,
        cancel_from: date | None = None,
    ) -> int:
        """
        Move every ACTIVE enrollment of `student` to `status`, and return how
        many were moved.

        `cancel_pending_periodic=True` also cancels the closed enrollments'
        PENDING monthly/quarterly payments — the recurring schedule the closed
        enrollment issued. Without it, a plan change that switches modality
        double-bills the overlap: the old enrollment's pending March mensualidad
        survives while the new quarterly enrollment re-bills March inside its
        first quarter, and neither the Python idempotency check nor
        `unique_pending_periodic_payment_per_month` can see the collision
        because the two rows carry different `payment_type`s. Completed money is
        never touched, and matrícula (`enrollment`) / `other` payments are not
        part of the recurring schedule so they stay owed.

        `keep_payment_type` preserves pending rows of that type. No caller passes
        it any more — `supersede_enrollment` explains why (it preserved exactly
        the full-price transition-month row that made a replacement's prorated
        first period disappear), and the handover is now always a month boundary
        that `cancel_from` already keeps the old rows on the safe side of. It is
        left in place because it is the correct behaviour for a caller that
        genuinely wants a same-cadence overlap kept.

        `cancel_from` limits the cancellation to rows due ON OR AFTER that date.
        Months already taught under the closed enrollment stay owed: a student
        moved to the waiting list on 20 March still owes March, and a
        re-enrollment starting 1 November must not void the September/October
        cuotas the old plan legitimately billed.

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

            if cancel_pending_periodic:
                # Row-by-row save, not `.update()`: Payment is audit-tracked and a
                # bulk UPDATE bypasses the pre/post_save receivers, so the cancel
                # would leave no AuditLog row and not bump `updated_at` — the same
                # reason this method itself loops (see docstring above).
                stale = enrollment.payments.filter(payment_status="pending", payment_type__in=PERIODIC_PAYMENT_TYPES)
                if keep_payment_type:
                    stale = stale.exclude(payment_type=keep_payment_type)
                if cancel_from is not None:
                    stale = stale.filter(due_date__gte=cancel_from)
                for payment in stale:
                    payment.payment_status = "cancelled"
                    payment.save(update_fields=["payment_status", "updated_at"])
                    logger.info(
                        "Cancelled pending %s payment %d of superseded enrollment %d.",
                        payment.payment_type,
                        int(payment.pk),
                        int(enrollment.pk),
                    )
        return closed

    @staticmethod
    def supersede_enrollment(student, current, *, requested_start=None, parent=None) -> date | None:
        """Close `current` so a replacement can start without mis-billing a month.

        THE canonical plan-transition. Three call sites go through it — the
        modality endpoint (`/api/students/<id>/modality/`), `StudentUpdateView`'s
        plan change and `enroll_student`'s "Nueva matrícula" modal — because they
        manage the same two hazards and had drifted into three different wrong
        answers about them.

        Returns the date the replacement must start on, which the caller must use
        verbatim (it is not always `requested_start` — see
        `PaymentService.transition_start_date`), or **None** when every remaining
        month of the course is already invoiced. On None NOTHING is written: the
        caller refuses the change rather than issuing an enrollment that could
        only re-bill a month somebody has already been asked to pay.

        What it does, in order and for a reason:

        1. **Resolve the handover date** before touching anything, so the refusal
           above cannot leave a half-applied change behind.
        2. **Bill what the closing enrollment taught and never invoiced**
           (`PaymentService.close_out_periods`). `generate_payments` visits only
           ACTIVE enrollments, so a month left open here can never be back-filled
           — the replacement's schedule starts later than it.
        3. **Close it, cancelling its pending periodic rows due on or after the
           handover month.** Those are the schedule of a plan that stops here,
           and under a cadence change they carry a different `payment_type`,
           invisible to both the month-level idempotency check and
           `unique_pending_periodic_payment_per_month`. Months before the
           handover stay owed — a student taught in September owes September —
           and COMPLETED money is never touched.

        `keep_payment_type` is deliberately NOT used any more. It existed to stop
        a same-cadence change cancelling rows the replacement would re-bill, but
        it preserved the very full-price transition-month row that made the
        replacement's prorated first period vanish. The handover is now always a
        month boundary the closing rows sit before, so there is nothing to keep.
        """
        from billing.services.payment_service import PaymentService

        requested_start = requested_start or date.today()
        effective_start = PaymentService.transition_start_date(student, requested_start, closing=current)
        if effective_start is None:
            return None

        if current is None:
            return effective_start

        if parent is None and not student.is_adult:
            # The titular of any payment created below, picked with the same
            # explicit ordering the payment generators use — an unordered
            # `.first()` can name a different parent for the same student.
            parent = student.parents.order_by("id").first()

        PaymentService.close_out_periods(current, parent=parent, until=effective_start)
        EnrollmentService.close_active_enrollments(
            student,
            "finished",
            cancel_pending_periodic=True,
            cancel_from=date(effective_start.year, effective_start.month, 1),
        )
        return effective_start

    @staticmethod
    def replicate_enrollment(source, *, start_date, payment_modality=None, schedule_type=None):
        """Issue a fresh Enrollment carrying `source`'s plan, starting on `start_date`.

        For changes that have no form behind them — today only the payment
        modality. `enrollment_date` is the anchor EVERY billing decision reads
        (`billing_periods`, `proration_fraction`, the matrícula due date), so
        editing a plan in place silently re-interprets the whole schedule that has
        already been billed against the old anchor. Superseding keeps the old row
        as the record of what was charged under it.

        The price is re-derived from `SiteConfiguration` for the NEW cadence,
        because `final_amount` is the price of one period and a month is not a
        quarter. A hand-priced (`special`) enrollment is the exception and is
        carried over verbatim: that figure was negotiated with the family and only
        a human may restate it per period.
        """
        config = SiteConfiguration.get_config()
        modality = payment_modality or source.payment_modality
        schedule = schedule_type or source.schedule_type
        academic_year = enrollment_academic_year(start_date)
        start_year = int(academic_year.split("-")[0])
        end_year = int(academic_year.split("-")[1])

        if source.is_hand_priced:
            base_amount = source.enrollment_amount
            discount_pct = source.discount_percentage
            final_amount = source.final_amount
        else:
            base_amount = period_base_amount(config, schedule, modality)
            discount_pct, final_amount = EnrollmentService._apply_discounts(
                config,
                base_amount,
                source.has_language_cheque,
                source.is_sibling_discount,
                schedule == "adult_group",
                modality,
            )

        enrollment = Enrollment(
            student=source.student,
            enrollment_type=source.enrollment_type,
            enrollment_period_start=academic_year_start_date(start_year),
            enrollment_period_end=academic_year_end_date(end_year),
            academic_year=academic_year,
            schedule_type=schedule,
            payment_modality=modality,
            has_language_cheque=source.has_language_cheque,
            is_sibling_discount=source.is_sibling_discount,
            enrollment_amount=base_amount,
            discount_percentage=discount_pct,
            final_amount=final_amount,
            status="active",
            enrollment_date=start_date,
            notes=source.notes,
        )
        enrollment.save()
        return enrollment

    @staticmethod
    def change_payment_modality(student, enrollment, modality, parent=None):
        """Move `student` onto a different payment cadence. Returns (replacement, start).

        Returns `(None, None)` when the change cannot take effect this course.

        The endpoint used to flip `payment_modality` on the live row and clear
        `final_amount` so `save()` re-derived it. That kept the ORIGINAL
        `enrollment_date` as the anchor, so the new cadence's `billing_periods`
        reached back over months already collected under the old one — and the
        `(student, payment_type, due month)` idempotency cannot see them, because
        `payment_type` is one of its keys. Sep and Oct collected monthly, flipped
        to quarterly on 15 November, and the next `generate_payments` run
        invoiced a full-price Sep-Nov quarter: two months collected twice, with
        the database constraint unable to help (pending-only, and keyed on
        `payment_type`). The reverse — a collected quarter, then monthly — back-
        filled three paid months.
        """
        from billing.services.payment_service import PaymentService

        if parent is None and not student.is_adult:
            parent = student.parents.order_by("id").first()

        with transaction.atomic():
            effective_start = EnrollmentService.supersede_enrollment(
                student, enrollment, requested_start=date.today(), parent=parent
            )
            if effective_start is None:
                return None, None

            replacement = EnrollmentService.replicate_enrollment(
                enrollment, start_date=effective_start, payment_modality=modality
            )
            PaymentService.schedule_academic_year_payments(replacement, parent)

        return replacement, effective_start

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
            # Shared derivation, not a fourth copy of "three months minus the
            # quarterly percentage" — see `quarterly_price_from_monthly`.
            return quarterly_price_from_monthly(config.full_time_monthly_fee, config), "full_time", "quarterly"
        return config.full_time_monthly_fee, "full_time", "monthly"

    @staticmethod
    def _apply_discounts(config, base_amount, has_lc, has_sibling, is_adult, payment_modality):
        """
        Apply discounts and return (discount_pct, final_amount).

        The €0.01 floor and the HALF_UP quantize both come from
        `pricing_service.round_money`, the single money rounding in billing. They
        used to be spelled out here, which made this the second of three copies
        (the payment generator and `Enrollment.save()` held the others) — and the
        rounding has to match exactly, or a half-cent intermediate (quarterly +
        sibling on the default prices lands on 146.205) stores 146.20 on the ficha
        and bills 146.21 on the invoice.
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

        return discount_pct, round_money(final_amount)
