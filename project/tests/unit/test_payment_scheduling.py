"""Unit tests for PaymentService.schedule_academic_year_payments.

Verifies that enrolling a student schedules the full academic year of pending
periodic payments (monthly Sep–Jun or quarterly Oct/Jan/Apr), due at period
end, and that re-running is idempotent.
"""

from datetime import date
from decimal import Decimal

import pytest

from billing.models import Enrollment, Payment
from billing.services.payment_service import PaymentService

pytestmark = pytest.mark.django_db


class TestScheduleAcademicYearPayments:
    def test_monthly_generates_full_year(self, active_enrollment, parent):
        created = PaymentService.schedule_academic_year_payments(active_enrollment, parent)
        assert created == 10  # Sep–Jun inclusive

        monthly = Payment.objects.filter(enrollment=active_enrollment, payment_type="monthly")
        assert monthly.count() == 10
        assert all(p.payment_status == "pending" for p in monthly)

        due = sorted(monthly.values_list("due_date", flat=True))
        assert due[0] == date(2025, 9, 30)  # due at month end
        assert due[-1] == date(2026, 6, 30)

    def test_idempotent(self, active_enrollment, parent):
        PaymentService.schedule_academic_year_payments(active_enrollment, parent)
        second = PaymentService.schedule_academic_year_payments(active_enrollment, parent)
        assert second == 0
        assert Payment.objects.filter(enrollment=active_enrollment, payment_type="monthly").count() == 10

    def test_quarterly_generates_three(self, student, enrollment_type_quarterly, site_config, parent):
        enrollment = Enrollment.objects.create(
            student=student,
            enrollment_type=enrollment_type_quarterly,
            enrollment_period_start=date(2025, 9, 15),
            enrollment_period_end=date(2026, 6, 27),
            academic_year="2025-2026",
            schedule_type="full_time",
            payment_modality="quarterly",
            enrollment_amount=Decimal("162.00"),
            discount_percentage=Decimal("0.00"),
            final_amount=Decimal("162.00"),
            status="active",
            enrollment_date=date(2025, 9, 1),
        )
        created = PaymentService.schedule_academic_year_payments(enrollment, parent)
        assert created == 3
        q = Payment.objects.filter(enrollment=enrollment, payment_type="quarterly")
        assert sorted(p.due_date.month for p in q) == [1, 4, 10]

    def test_inactive_student_is_skipped(self, active_enrollment, parent):
        active_enrollment.student.active = False
        active_enrollment.student.save(update_fields=["active"])
        created = PaymentService.schedule_academic_year_payments(active_enrollment, parent)
        assert created == 0
