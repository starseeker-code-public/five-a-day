"""
Management command to generate automatic periodic payments.

Payments are created on the FIRST day of their period and fall due on its LAST day.
Monthly students get one per teaching month (Sep-Jun); quarterly students get
consecutive 3-month blocks anchored to the month they enrolled, so a mid-year
joiner is billed from the day they start instead of falling into a gap between
fixed calendar quarters.

The command back-fills: any period that has started without a payment is created,
so a run the scheduler missed is repaired on the next one.

Usage:
    python manage.py generate_payments              # Generate for current month
    python manage.py generate_payments --month 10 --year 2025  # Specific month
    python manage.py generate_payments --dry-run    # Preview without creating
"""

import calendar
from datetime import date

from django.core.management.base import BaseCommand
from django.db.models import Prefetch

from billing.models import (
    Enrollment,
    SiteConfiguration,
    academic_year_for_month,
)
from billing.services.payment_service import (
    MONTH_NAMES_ES,
    PaymentService,
)
from students.models import Parent


class Command(BaseCommand):
    help = "Generate automatic periodic payments for enrolled students"

    def add_arguments(self, parser):
        parser.add_argument(
            "--month", type=int, default=None, help="Month to generate payments for (1-12). Defaults to current month."
        )
        parser.add_argument(
            "--year", type=int, default=None, help="Year to generate payments for. Defaults to current year."
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Preview payments that would be created without saving."
        )

    def handle(self, *args, **options):
        today = date.today()
        month = options["month"] or today.month
        year = options["year"] or today.year
        dry_run = options["dry_run"]

        config = SiteConfiguration.get_config()
        # The course this teaching month belongs to — NOT the one enrolment is
        # currently open for. In May/June those differ, and using the enrolment
        # year here would match no active enrollment and generate nothing.
        academic_year = academic_year_for_month(date(year, month, 1))

        enrollments = (
            Enrollment.objects.filter(
                status="active",
                academic_year=academic_year,
            )
            # enrollment_type is read for every enrollment (a `special` matrícula is
            # billed at its own hand-set price), so join it rather than paying a
            # query per student.
            .select_related("student", "student__group", "enrollment_type")
            # Ordered explicitly: this prefetch decides which parent becomes the
            # TITULAR on every payment created below, and the `.first()` it replaces
            # sorted by pk. An unordered prefetch would hand that to DB row order.
            .prefetch_related(Prefetch("student__parents", queryset=Parent.objects.order_by("id")))
        )

        # Every enrollment's already-billed due months, in ONE query. Without this
        # `pending_periods` spends a `SELECT payments` per enrollment — measured at
        # exactly 1 each, so ~2,000 round trips per run at the roll this academy is
        # sized for. `enrollments` is materialised first so the map covers precisely
        # the rows the loop will visit.
        enrollments = list(enrollments)
        billed = PaymentService.billed_months_map({e.student_id for e in enrollments})

        created_count = 0
        skipped_count = 0

        # Every enrollment goes through PaymentService.schedule_academic_year_payments,
        # the same call the enrollment form makes. It creates any period that has
        # already started and has no payment yet, so this command both opens the new
        # month/quarter and back-fills anything a missed run left behind.
        as_of = date(year, month, calendar.monthrange(year, month)[1])

        for enrollment in enrollments:
            student = enrollment.student
            if not student.active:
                continue

            parent = None
            if not student.is_adult:
                # NOT `.parents.first()`. `QuerySet.first()` on an unordered queryset
                # calls `order_by("pk")`, which clones the queryset and drops
                # `_result_cache` — so it re-queries and the
                # `prefetch_related("student__parents")` above is thrown away, one
                # round trip per enrollment (243 for 240 students, against 2). No
                # model here sets `Meta.ordering`, so this is always the case.
                parent = next(iter(student.parents.all()), None)
                if not parent:
                    # Bill anyway. `Payment.parent` is nullable and `Payment.clean()`
                    # only validates the relationship when a parent is present —
                    # `create_payment` relies on exactly that for adult students. A
                    # child can legitimately lack a Parent row (a waiting-list entry
                    # promoted from the ficha keeps its contact on
                    # `waiting_contact_*`), and skipping meant that student accrued
                    # ZERO mensualidades for the whole year while the ficha showed
                    # the family as up to date.
                    self.stdout.write(
                        self.style.WARNING(f"  AVISO {student.full_name}: sin padre/tutor — se factura sin titular")
                    )

            if dry_run:
                # Same selection the real run uses — PaymentService.pending_periods
                # is the single decision point. Re-deriving it here is how the
                # preview drifted from the run it was previewing: it dropped the
                # "first period is always issued" rule and matched existing
                # payments on the exact due date instead of the due month/year.
                quarterly = enrollment.payment_modality == "quarterly"
                periods = PaymentService.pending_periods(
                    enrollment,
                    as_of=as_of,
                    billed_months=billed.setdefault(
                        (enrollment.student_id, "quarterly" if quarterly else "monthly"), set()
                    ),
                )
                for period in periods:
                    amount = PaymentService.calculate_period_amount(
                        enrollment,
                        config,
                        [m for m, _ in period["months"]],
                        period["fraction"],
                        quarterly,
                    )
                    concept = PaymentService.period_concept(period, quarterly)
                    self.stdout.write(f"  [DRY RUN] {student.full_name}: {concept} - EUR{amount:.2f}")
                    created_count += 1
                continue

            payment_type = "quarterly" if enrollment.payment_modality == "quarterly" else "monthly"
            made = PaymentService.schedule_academic_year_payments(
                enrollment,
                parent,
                as_of=as_of,
                billed_months=billed.setdefault((enrollment.student_id, payment_type), set()),
            )
            created_count += made
            if made == 0:
                skipped_count += 1

        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Payment generation complete for {MONTH_NAMES_ES.get(month, month)} {year}: "
                f"{created_count} created, {skipped_count} skipped"
            )
        )
