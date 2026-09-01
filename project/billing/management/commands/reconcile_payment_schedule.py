"""Reconcile existing enrollments against the v1.22.0 billing schedule.

Quarters used to be pinned to a fixed Oct/Jan/Apr calendar. Two silent gaps came
out of that: September was outside every quarter, so quarterly students never
paid for it, and a student who enrolled mid-quarter was not billed for the months
before the next fixed quarter fell due. Rows already in the database still carry
the old shape, and re-running the generator would NOT repair them — the
idempotency check matches on due month/year, and the new due dates differ, so a
plain re-run would happily create a second, overlapping set of payments.

This command reconciles instead of regenerating:

  * Money that was actually COLLECTED is never touched. An enrollment with any
    completed periodic payment is reported for review and left alone unless
    --force is passed, because rewriting a settled schedule corrupts the books.
  * Gaps (a period the new schedule wants, with no payment at all) are created.
  * Stale pending rows (a payment whose due date matches no period) are cancelled
    rather than deleted, so the history stays auditable.

It is a DRY RUN unless --apply is given.

    python manage.py reconcile_payment_schedule                    # report only
    python manage.py reconcile_payment_schedule --apply            # fill gaps
    python manage.py reconcile_payment_schedule --apply --cancel-stale
    python manage.py reconcile_payment_schedule --academic-year 2025-2026
"""

from decimal import ROUND_HALF_UP, Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from billing.models import Enrollment, Payment, SiteConfiguration
from billing.services.payment_service import PaymentService

PERIODIC_TYPES = ("monthly", "quarterly")


class Command(BaseCommand):
    help = "Reconcile existing payments against the enrollment-anchored billing schedule"

    def add_arguments(self, parser):
        parser.add_argument("--academic-year", default=None, help='Limit to one year, e.g. "2025-2026".')
        parser.add_argument("--apply", action="store_true", help="Write changes. Without it, nothing is saved.")
        parser.add_argument(
            "--cancel-stale",
            action="store_true",
            help="Also cancel pending payments whose due date matches no period in the new schedule.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Reconcile even enrollments that already have completed payments. Use with care.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        cancel_stale = options["cancel_stale"]
        force = options["force"]

        enrollments = Enrollment.objects.filter(status="active").select_related(
            "student", "enrollment_type", "student__group"
        )
        if options["academic_year"]:
            enrollments = enrollments.filter(academic_year=options["academic_year"])

        config = SiteConfiguration.get_config()
        created = cancelled = reviewed = untouched = 0

        for enrollment in enrollments.prefetch_related("student__parents"):
            student = enrollment.student
            if not student.active:
                continue

            quarterly = enrollment.payment_modality == "quarterly"
            payment_type = "quarterly" if quarterly else "monthly"

            # Cancelled rows are INCLUDED. They are excluded from `stale` and
            # `settled` below, but they must still occupy their due date: a
            # payment an admin soft-deleted through `deactivate_payment` is
            # cancelled, not absent, and dropping it here made its period look
            # like a gap so the next run re-created the very row they removed.
            # The generator's own idempotency check counts cancelled payments
            # too — the two must agree.
            existing = list(
                Payment.objects.filter(
                    student=student,
                    enrollment=enrollment,
                    payment_type__in=PERIODIC_TYPES,
                )
            )
            settled = [p for p in existing if p.payment_status == "completed"]

            periods = PaymentService.billing_periods(enrollment)
            wanted = {p["due"]: p for p in periods}
            have = {p.due_date for p in existing}

            gaps = [due for due in wanted if due not in have]
            stale = [p for p in existing if p.due_date not in wanted and p.payment_status == "pending"]

            if not gaps and not stale:
                untouched += 1
                continue

            if settled and not force:
                reviewed += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"  REVIEW {student.full_name} ({payment_type}): "
                        f"{len(settled)} payment(s) already collected — "
                        f"{len(gaps)} gap(s), {len(stale)} stale. Left untouched."
                    )
                )
                for due in sorted(gaps):
                    self.stdout.write(f"      gap   {due}  {PaymentService.period_concept(wanted[due], quarterly)}")
                for payment in sorted(stale, key=lambda p: p.due_date):
                    self.stdout.write(f"      stale {payment.due_date}  {payment.concept} (EUR {payment.amount})")
                continue

            parent = None if student.is_adult else student.parents.first()

            with transaction.atomic():
                for due in sorted(gaps):
                    period = wanted[due]
                    amount = PaymentService.calculate_period_amount(
                        enrollment,
                        config,
                        [m for m, _ in period["months"]],
                        period["fraction"],
                        quarterly,
                    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    concept = PaymentService.period_concept(period, quarterly)

                    self.stdout.write(f"  + {student.full_name}: {concept} due {due} — EUR {amount}")
                    if apply_changes:
                        Payment.objects.create(
                            student=student,
                            parent=parent,
                            enrollment=enrollment,
                            payment_type=payment_type,
                            payment_method="transfer",
                            amount=amount,
                            payment_status="pending",
                            due_date=due,
                            concept=concept,
                        )
                    created += 1

                if cancel_stale:
                    for payment in sorted(stale, key=lambda p: p.due_date):
                        self.stdout.write(
                            f"  - {student.full_name}: cancelling {payment.concept} "
                            f"due {payment.due_date} (EUR {payment.amount})"
                        )
                        if apply_changes:
                            payment.payment_status = "cancelled"
                            payment.save(update_fields=["payment_status", "updated_at"])
                        cancelled += 1

                if not apply_changes:
                    transaction.set_rollback(True)

        prefix = "" if apply_changes else "[DRY RUN] "
        style = self.style.SUCCESS if apply_changes else self.style.WARNING
        self.stdout.write(
            style(
                f"{prefix}Reconciliation complete: {created} payment(s) to create, "
                f"{cancelled} to cancel, {reviewed} enrollment(s) needing review, "
                f"{untouched} already correct."
            )
        )
        if not apply_changes:
            self.stdout.write("Nothing was saved. Re-run with --apply to write these changes.")
