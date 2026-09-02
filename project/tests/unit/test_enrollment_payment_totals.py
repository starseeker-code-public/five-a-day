"""`Enrollment` must report what a family actually owes.

`is_paid` summed **every** completed payment on the enrollment — the matrícula
and each month's cuota together — and compared the total to `final_amount`,
which is the price of **one period**. The two sides of that comparison were
different units, so the figure could not mean anything:

* a monthly student owing 520 EUR across ten periods reported `is_paid=True`
  and `remaining_amount=0.00` the moment one 54 EUR month was collected;
* a 40 EUR matrícula on its own reported `remaining_amount=14.00`, i.e. the
  enrollment fee counted against a monthly fee.

It is replaced by `payment_totals()` and the three properties derived from it.
The scenario at the bottom of this file is the measured one that exposed it.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from billing.models import Enrollment, Payment
from billing.services.payment_service import PaymentService

pytestmark = pytest.mark.django_db


def _payment(enrollment, student, *, status, days, amount="54.00", payment_type="monthly"):
    due = date.today() + timedelta(days=days)
    return Payment.objects.create(
        student=student,
        enrollment=enrollment,
        payment_type=payment_type,
        payment_method="transfer",
        amount=Decimal(amount),
        payment_status=status,
        due_date=due,
        payment_date=due if status == "completed" else None,
        concept="Mensualidad",
    )


class TestPaymentTotals:
    def test_nothing_billed(self, active_enrollment):
        totals = active_enrollment.payment_totals()

        assert totals == (Decimal("0.00"), Decimal("0.00"), 0)
        assert active_enrollment.is_up_to_date is True

    def test_a_period_still_open_is_outstanding_but_not_overdue(self, active_enrollment, student):
        _payment(active_enrollment, student, status="pending", days=10)

        totals = active_enrollment.payment_totals()
        assert totals.outstanding == Decimal("54.00")
        assert totals.overdue == Decimal("0.00")
        assert active_enrollment.is_up_to_date is True

    def test_a_payment_past_its_due_date_is_overdue(self, active_enrollment, student):
        _payment(active_enrollment, student, status="pending", days=-1)

        assert active_enrollment.overdue_amount == Decimal("54.00")
        assert active_enrollment.is_up_to_date is False

    def test_arrears_and_an_open_period_are_reported_separately(self, active_enrollment, student):
        _payment(active_enrollment, student, status="pending", days=-40)
        _payment(active_enrollment, student, status="pending", days=10)

        totals = active_enrollment.payment_totals()
        assert totals.overdue == Decimal("54.00"), "only the late one is arrears"
        assert totals.outstanding == Decimal("108.00"), "both are invoiced and uncollected"
        assert active_enrollment.is_up_to_date is False

    def test_completed_money_is_neither_owed_nor_late(self, active_enrollment, student):
        _payment(active_enrollment, student, status="completed", days=-5)

        totals = active_enrollment.payment_totals()
        assert (totals.overdue, totals.outstanding, totals.billed) == (Decimal("0.00"), Decimal("0.00"), 1)
        assert active_enrollment.is_up_to_date is True

    @pytest.mark.parametrize("status", ["cancelled", "failed", "refunded"])
    def test_money_that_will_never_arrive_is_not_owed(self, active_enrollment, student, status):
        """Matches `LIVE_PAYMENT_STATUSES`: a cancelled duplicate must not make
        a family look like it is in arrears."""
        _payment(active_enrollment, student, status=status, days=-30)

        totals = active_enrollment.payment_totals()
        assert totals.overdue == Decimal("0.00")
        assert totals.outstanding == Decimal("0.00")
        assert totals.billed == 1, "the row still exists, so the enrollment has been billed"

    def test_the_matricula_is_not_measured_against_a_monthly_fee(self, active_enrollment, student):
        """The old `remaining_amount` answered 14.00 here — a 40 EUR enrollment
        fee subtracted from a 54 EUR monthly price."""
        _payment(active_enrollment, student, status="completed", days=-5, amount="40.00", payment_type="enrollment")

        assert active_enrollment.outstanding_amount == Decimal("0.00")
        assert active_enrollment.is_up_to_date is True

    def test_costs_one_query(self, active_enrollment, student, django_assert_num_queries):
        # Two pending rows, deliberately in DIFFERENT months. Both were
        # `today +/- a few days`, which put them in the same month whenever the
        # suite ran near a month boundary — two pending monthly payments for one
        # student in one month is the double-billing that
        # `unique_pending_periodic_payment_per_month` now forbids, so this fixture
        # was only ever valid by calendar luck. What the test needs is two pending
        # rows, not two in one month.
        _payment(active_enrollment, student, status="pending", days=-45)
        _payment(active_enrollment, student, status="pending", days=10)

        with django_assert_num_queries(1):
            active_enrollment.payment_totals()


class TestTheScenarioThatExposedIt:
    """A full elapsed academic year: ten monthly periods worth 520 EUR."""

    @pytest.fixture
    def elapsed_year(self, student, enrollment_type_new_student, site_config):
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

    def test_one_month_paid_does_not_settle_the_year(self, elapsed_year, parent):
        PaymentService.schedule_academic_year_payments(elapsed_year, parent, as_of=date(2026, 6, 30))
        months = Payment.objects.filter(enrollment=elapsed_year, payment_type="monthly").order_by("due_date")
        assert months.count() == 10
        assert sum(p.amount for p in months) == Decimal("520.00")

        first = months.first()
        first.payment_status = "completed"
        first.payment_date = first.due_date
        first.save()

        totals = elapsed_year.payment_totals()
        assert elapsed_year.is_up_to_date is False, "nine months are still owed"
        assert totals.overdue == Decimal("466.00")
        assert months.filter(payment_status="pending").count() == 9
