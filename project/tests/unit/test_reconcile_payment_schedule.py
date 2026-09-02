"""Tests for the reconcile_payment_schedule command.

The dangerous failure mode is double-billing: the old fixed quarters have
different due dates from the new anchored ones, so a naive re-run of the
generator creates a second overlapping set. Reconciliation must fill gaps
without ever charging twice, and must never rewrite money already collected.
"""

from datetime import date
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command

from billing.models import Enrollment, Payment
from billing.services.payment_service import PaymentService

pytestmark = pytest.mark.django_db


def _enrollment(student, etype, modality="quarterly", joined=date(2025, 9, 1)):
    return Enrollment.objects.create(
        student=student,
        enrollment_type=etype,
        enrollment_period_start=date(2025, 9, 15),
        enrollment_period_end=date(2026, 6, 26),
        academic_year="2025-2026",
        schedule_type="full_time",
        payment_modality=modality,
        enrollment_amount=Decimal("54.00"),
        discount_percentage=Decimal("0.00"),
        final_amount=Decimal("54.00"),
        status="active",
        enrollment_date=joined,
    )


def _legacy_quarterly(enrollment, parent, status="pending"):
    """The rows the OLD fixed Oct/Jan/Apr calendar produced."""
    for month, year, concept in [
        (10, 2025, "Trimestre 1er Trimestre (Oct-Dic) 2025"),
        (1, 2026, "Trimestre 2do Trimestre (Ene-Mar) 2026"),
        (4, 2026, "Trimestre 3er Trimestre (Abr-Jun) 2026"),
    ]:
        last = {10: 31, 1: 31, 4: 30}[month]
        Payment.objects.create(
            student=enrollment.student,
            parent=parent,
            enrollment=enrollment,
            payment_type="quarterly",
            payment_method="transfer",
            amount=Decimal("153.90"),
            payment_status=status,
            due_date=date(year, month, last),
            concept=concept,
        )


def _run(**flags):
    out = StringIO()
    call_command("reconcile_payment_schedule", stdout=out, **flags)
    return out.getvalue()


class TestDryRunIsSafe:
    def test_dry_run_writes_nothing(self, student, enrollment_type_returning_student, site_config, parent):
        e = _enrollment(student, enrollment_type_returning_student)
        _legacy_quarterly(e, parent)
        before = set(Payment.objects.values_list("id", flat=True))

        output = _run()

        assert set(Payment.objects.values_list("id", flat=True)) == before
        assert "DRY RUN" in output
        assert "Nothing was saved" in output


class TestCollectedMoneyIsNeverTouched:
    def test_enrollment_with_a_completed_payment_is_reported_not_changed(
        self, student, enrollment_type_returning_student, site_config, parent
    ):
        e = _enrollment(student, enrollment_type_returning_student)
        _legacy_quarterly(e, parent, status="completed")

        output = _run(apply=True, cancel_stale=True)

        assert "REVIEW" in output
        assert Payment.objects.filter(enrollment=e, payment_status="completed").count() == 3
        # Nothing created, nothing cancelled.
        assert Payment.objects.filter(enrollment=e).count() == 3
        assert not Payment.objects.filter(enrollment=e, payment_status="cancelled").exists()

    def test_force_is_required_to_touch_them(self, student, enrollment_type_returning_student, site_config, parent):
        e = _enrollment(student, enrollment_type_returning_student)
        _legacy_quarterly(e, parent, status="completed")

        _run(apply=True, force=True)
        # With --force the missing periods are added; the completed rows survive.
        assert Payment.objects.filter(enrollment=e, payment_status="completed").count() == 3
        assert Payment.objects.filter(enrollment=e, payment_status="pending").exists()


class TestGapsAreFilledWithoutDoubleBilling:
    def test_legacy_pending_schedule_is_replaced_not_duplicated(
        self, student, enrollment_type_returning_student, site_config, parent
    ):
        e = _enrollment(student, enrollment_type_returning_student)
        _legacy_quarterly(e, parent)

        _run(apply=True, cancel_stale=True)

        live = Payment.objects.filter(enrollment=e).exclude(payment_status="cancelled")
        expected = {p["due"] for p in PaymentService.billing_periods(e)}
        assert {p.due_date for p in live} == expected

        # The three legacy rows are cancelled, not deleted — the history stays.
        assert Payment.objects.filter(enrollment=e, payment_status="cancelled").count() == 3

    def test_september_gap_is_filled_for_quarterly_students(
        self, student, enrollment_type_returning_student, site_config, parent
    ):
        """September fell outside every fixed quarter, so it was never billed."""
        e = _enrollment(student, enrollment_type_returning_student)
        _legacy_quarterly(e, parent)

        _run(apply=True, cancel_stale=True)

        # The first anchored block starts in September and falls due 30 Nov.
        september_block = Payment.objects.get(enrollment=e, due_date=date(2025, 11, 30))
        assert "Septiembre" in september_block.concept

    def test_running_twice_changes_nothing_the_second_time(
        self, student, enrollment_type_returning_student, site_config, parent
    ):
        e = _enrollment(student, enrollment_type_returning_student)
        _legacy_quarterly(e, parent)

        _run(apply=True, cancel_stale=True)
        snapshot = set(Payment.objects.exclude(payment_status="cancelled").values_list("id", flat=True))

        output = _run(apply=True, cancel_stale=True)

        assert set(Payment.objects.exclude(payment_status="cancelled").values_list("id", flat=True)) == snapshot
        assert "0 payment(s) to create" in output

    def test_already_correct_enrollment_is_left_alone(
        self, student, enrollment_type_returning_student, site_config, parent
    ):
        e = _enrollment(student, enrollment_type_returning_student, modality="monthly")
        PaymentService.schedule_academic_year_payments(e, parent, as_of=date(2026, 6, 30))
        before = set(Payment.objects.values_list("id", flat=True))

        output = _run(apply=True, cancel_stale=True)

        assert set(Payment.objects.values_list("id", flat=True)) == before
        assert "already correct" in output


class TestScoping:
    def test_academic_year_filter_skips_other_years(
        self, student, enrollment_type_returning_student, site_config, parent
    ):
        e = _enrollment(student, enrollment_type_returning_student)
        _legacy_quarterly(e, parent)
        before = set(Payment.objects.values_list("id", flat=True))

        _run(apply=True, cancel_stale=True, academic_year="2099-2100")

        assert set(Payment.objects.values_list("id", flat=True)) == before


class TestSoftDeletedPaymentsAreNotResurrected:
    """`deactivate_payment` cancels rather than deletes.

    Excluding cancelled rows from the "what already exists" set made their
    period look like a gap, so the next `--apply` re-created exactly the row an
    admin had removed. The generator's own idempotency check counts cancelled
    payments; the two have to agree.
    """

    def _scheduled(self, enrollment, parent):
        PaymentService.schedule_academic_year_payments(enrollment, parent, as_of=date(2026, 6, 30))
        return list(Payment.objects.filter(enrollment=enrollment, payment_type="monthly").order_by("due_date"))

    @pytest.fixture
    def monthly_enrollment(self, student, enrollment_type_new_student, site_config):
        return Enrollment.objects.create(
            student=student,
            enrollment_type=enrollment_type_new_student,
            enrollment_period_start=date(2025, 9, 15),
            enrollment_period_end=date(2026, 6, 27),
            academic_year="2025-2026",
            schedule_type="full_time",
            payment_modality="monthly",
            enrollment_amount=Decimal("54.00"),
            discount_percentage=Decimal("0.00"),
            final_amount=Decimal("54.00"),
            status="active",
            enrollment_date=date(2025, 9, 1),
        )

    def test_a_cancelled_payment_is_left_alone(self, monthly_enrollment, parent):
        victim = self._scheduled(monthly_enrollment, parent)[2]
        victim.payment_status = "cancelled"
        victim.save(update_fields=["payment_status"])
        before = Payment.objects.filter(enrollment=monthly_enrollment).count()

        call_command("reconcile_payment_schedule", "--apply", stdout=StringIO())

        assert Payment.objects.filter(enrollment=monthly_enrollment).count() == before
        assert Payment.objects.filter(enrollment=monthly_enrollment, due_date=victim.due_date).count() == 1

    def test_a_genuine_gap_is_still_filled(self, monthly_enrollment, parent):
        removed = self._scheduled(monthly_enrollment, parent)[2]
        gap_due = removed.due_date
        removed.delete()

        call_command("reconcile_payment_schedule", "--apply", stdout=StringIO())

        assert Payment.objects.filter(
            enrollment=monthly_enrollment, due_date=gap_due, payment_status="pending"
        ).exists()
