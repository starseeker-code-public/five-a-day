"""Tests for the enrollment start date.

`EnrollmentForm.start_date` becomes `Enrollment.enrollment_date`, the academic
year derives from it, and billing begins at the start date instead of the day
the ficha was created — a student signed up today for a 1 November start is
billed from November.

All dates here use an ELAPSED academic year (2020-2021) so every period has
started and the assertions hold on any run date — the same anchoring rationale
as `elapsed_year_enrollment` in test_payment_scheduling.py.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest

from billing.forms import EnrollmentForm
from billing.models import Payment, current_academic_year
from billing.services.enrollment_service import EnrollmentService
from billing.services.payment_service import PaymentService

pytestmark = pytest.mark.django_db


class TestCreateEnrollmentStartDate:
    def test_start_date_sets_enrollment_date_and_academic_year(self, student, enrollment_type_new_student, site_config):
        enrollment = EnrollmentService.create_enrollment(
            student, {"enrollment_plan": "monthly_full", "start_date": date(2020, 11, 15)}
        )
        assert enrollment.enrollment_date == date(2020, 11, 15)
        assert enrollment.academic_year == "2020-2021"

    def test_no_start_date_defaults_to_today(self, student, enrollment_type_new_student, site_config):
        enrollment = EnrollmentService.create_enrollment(student, {"enrollment_plan": "monthly_full"})
        assert enrollment.enrollment_date == date.today()
        assert enrollment.academic_year == current_academic_year()

    def test_form_passes_start_date_through(self, student, enrollment_type_new_student, site_config, monkeypatch):
        # Widen the typo-year window to include the elapsed anchor course —
        # the window rule itself is covered in TestStartDateWindow below.
        from billing.models import relevant_academic_years as _real

        monkeypatch.setattr(
            "billing.models.relevant_academic_years",
            lambda reference_date=None: ["2020-2021", *_real(reference_date)],
        )
        form = EnrollmentForm({"enrollment_plan": "monthly_full", "start_date": "2020-11-15"})
        assert form.is_valid(), form.errors
        enrollment = form.create_enrollment(student)
        assert enrollment.enrollment_date == date(2020, 11, 15)


class TestStartDateWindow:
    """`clean_start_date` — the typo-year guard on NEW start dates."""

    def test_mistyped_past_year_rejected(self):
        form = EnrollmentForm({"enrollment_plan": "monthly_full", "start_date": "2019-09-01"})
        assert not form.is_valid()
        assert "curso actual" in " ".join(form.errors["start_date"])

    def test_far_future_year_rejected(self):
        form = EnrollmentForm({"enrollment_plan": "monthly_full", "start_date": "2039-09-01"})
        assert not form.is_valid()

    def test_today_accepted(self):
        form = EnrollmentForm({"enrollment_plan": "monthly_full", "start_date": date.today().isoformat()})
        assert form.is_valid(), form.errors

    def test_blank_accepted(self):
        form = EnrollmentForm({"enrollment_plan": "monthly_full"})
        assert form.is_valid(), form.errors

    def test_unchanged_current_start_bypasses_window(self):
        # Editing an enrollment from a past course POSTs its stored start date
        # back unchanged; the window must not reject it.
        old = date(2019, 9, 1)
        form = EnrollmentForm(
            {"enrollment_plan": "monthly_full", "start_date": old.isoformat()},
            current_start=old,
        )
        assert form.is_valid(), form.errors

    def test_summer_start_first_period_is_september(self, student, enrollment_type_new_student, site_config):
        """A July signup starts billing in September (the course's first
        teaching month), unprorated — there is nothing to prorate in July."""
        enrollment = EnrollmentService.create_enrollment(
            student, {"enrollment_plan": "monthly_full", "start_date": date(2020, 7, 10)}
        )
        assert enrollment.academic_year == "2020-2021"
        periods = PaymentService.billing_periods(enrollment)
        assert periods[0]["months"][0] == (9, 2020)
        assert periods[0]["fraction"] == Decimal("1")


class TestBillingStartsAtStartDate:
    def test_payments_begin_in_start_month_not_signup_month(
        self, student, parent, enrollment_type_new_student, site_config
    ):
        """Start 15 Nov 2020: the schedule runs Nov 2020 → Jun 2021 — nothing
        for Sep/Oct, and the first month is prorated 16/30 by the start day."""
        enrollment = EnrollmentService.create_enrollment(
            student, {"enrollment_plan": "monthly_full", "start_date": date(2020, 11, 15)}
        )
        created = PaymentService.schedule_academic_year_payments(enrollment, parent)
        assert created == 8  # Nov..Jun

        payments = list(Payment.objects.filter(student=student, payment_type="monthly").order_by("due_date"))
        due_months = [(p.due_date.month, p.due_date.year) for p in payments]
        assert due_months[0] == (11, 2020)
        assert (9, 2020) not in due_months
        assert (10, 2020) not in due_months

        # Proration reference is the START date (16 remaining days of 30).
        fee = enrollment.final_amount
        expected_first = (fee * Decimal(16) / Decimal(30)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert payments[0].amount == expected_first
        assert "(parcial)" in payments[0].concept
        # Every later month is the full fee (December carries no discounts).
        assert payments[1].amount == fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
