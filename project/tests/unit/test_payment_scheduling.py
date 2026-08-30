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

    def test_quarterly_generates_three(self, student, enrollment_type_returning_student, site_config, parent):
        enrollment = Enrollment.objects.create(
            student=student,
            enrollment_type=enrollment_type_returning_student,
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


class TestSpecialPriceIsBilled:
    """A `special` matrícula is billed at the price the admin typed.

    The custom amount was stored on the Enrollment correctly, but both generators
    re-read SiteConfiguration and billed the standard 1-day / 2-day fee, so the
    ficha and the payments disagreed for every period of the year.
    """

    @staticmethod
    def _special_enrollment(student, enrollment_type_special, modality, amount):
        return Enrollment.objects.create(
            student=student,
            enrollment_type=enrollment_type_special,
            enrollment_period_start=date(2025, 9, 15),
            enrollment_period_end=date(2026, 6, 27),
            academic_year="2025-2026",
            # A hand-priced student still attends a normal timetable — the
            # schedule type must NOT drag the standard price back in.
            schedule_type="full_time",
            payment_modality=modality,
            enrollment_amount=amount,
            discount_percentage=Decimal("0.00"),
            final_amount=amount,
            status="active",
            enrollment_date=date(2025, 9, 1),
        )

    def test_monthly_payments_use_the_custom_price(self, student, enrollment_type_special, site_config, parent):
        enrollment = self._special_enrollment(student, enrollment_type_special, "monthly", Decimal("35.00"))

        created = PaymentService.schedule_academic_year_payments(enrollment, parent)
        assert created == 10

        amounts = set(Payment.objects.filter(enrollment=enrollment).values_list("amount", flat=True))
        assert amounts == {Decimal("35.00")}
        assert site_config.full_time_monthly_fee not in amounts

    def test_june_discount_is_not_applied_on_top(self, student, enrollment_type_special, site_config, parent):
        """The negotiated price is the whole price — nothing is layered onto it."""
        site_config.june_discount = Decimal("10.00")
        site_config.save()
        enrollment = self._special_enrollment(student, enrollment_type_special, "monthly", Decimal("35.00"))

        PaymentService.schedule_academic_year_payments(enrollment, parent)
        june = Payment.objects.get(enrollment=enrollment, due_date__month=6)
        assert june.amount == Decimal("35.00")

    def test_quarterly_payments_use_the_custom_price(self, student, enrollment_type_special, site_config, parent):
        enrollment = self._special_enrollment(student, enrollment_type_special, "quarterly", Decimal("120.00"))

        created = PaymentService.schedule_academic_year_payments(enrollment, parent)
        assert created == 3
        assert set(Payment.objects.filter(enrollment=enrollment).values_list("amount", flat=True)) == {
            Decimal("120.00")
        }

    def test_sibling_and_cheque_discounts_are_not_re_applied(
        self, student, enrollment_type_special, site_config, parent
    ):
        """EnrollmentService already folded them into final_amount at creation."""
        enrollment = self._special_enrollment(student, enrollment_type_special, "monthly", Decimal("35.00"))
        enrollment.is_sibling_discount = True
        enrollment.has_language_cheque = True
        enrollment.save(update_fields=["is_sibling_discount", "has_language_cheque"])

        PaymentService.schedule_academic_year_payments(enrollment, parent)
        assert set(Payment.objects.filter(enrollment=enrollment).values_list("amount", flat=True)) == {Decimal("35.00")}

    def test_standard_enrollment_still_uses_the_configured_fee(self, active_enrollment, site_config, parent):
        PaymentService.schedule_academic_year_payments(active_enrollment, parent)
        september = Payment.objects.get(enrollment=active_enrollment, due_date__month=9)
        assert september.amount == site_config.full_time_monthly_fee

    def test_enrollment_service_end_to_end(self, student, enrollment_type_special, site_config, parent):
        """The price typed in the form is what the year of payments charges."""
        from billing.services.enrollment_service import EnrollmentService

        enrollment = EnrollmentService.create_enrollment(
            student,
            {
                "enrollment_plan": "monthly_full",
                "has_language_cheque": False,
                "is_sibling_discount": False,
                "is_special": True,
                "manual_amount": Decimal("42.50"),
            },
        )
        assert enrollment.enrollment_type.name == "special"
        assert enrollment.final_amount == Decimal("42.50")

        PaymentService.schedule_academic_year_payments(enrollment, parent)
        assert set(Payment.objects.filter(enrollment=enrollment).values_list("amount", flat=True)) == {
            enrollment.final_amount
        }

    def test_periodic_command_uses_the_custom_price(self, student_with_parent, enrollment_type_special, site_config):
        """`generate_payments` (the monthly cron) reads the same rule."""
        from django.core.management import call_command

        enrollment = self._special_enrollment(student_with_parent, enrollment_type_special, "monthly", Decimal("35.00"))
        call_command("generate_payments", month=10, year=2025)

        payment = Payment.objects.get(student=student_with_parent, payment_type="monthly", due_date__month=10)
        assert payment.amount == Decimal("35.00")
        assert payment.enrollment == enrollment
