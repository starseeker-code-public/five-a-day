"""Unit tests for PaymentService.schedule_academic_year_payments.

Payments are created on the FIRST day of their period and fall due on its LAST
day. Quarterly blocks are anchored to the month the student enrolled, not to a
fixed Oct/Jan/Apr calendar, and the first period is prorated by join date.
"""

from datetime import date
from decimal import Decimal

import pytest

from billing.models import Enrollment, Payment
from billing.services.payment_service import PaymentService

pytestmark = pytest.mark.django_db


@pytest.fixture
def elapsed_year_enrollment(student, enrollment_type_new_student, site_config):
    """A monthly enrollment whose academic year is entirely in the past.

    The generator only issues periods that have already STARTED, so "the whole
    Sep–Jun schedule is created" is only observable once the year has elapsed.
    The shared `active_enrollment` fixture deliberately tracks the *current*
    course (the student list filters on `relevant_academic_years()`), where at
    most a couple of periods have started — so these two tests pin their own
    finished year and assert the same thing on any day of any year.
    """
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


class TestScheduleAcademicYearPayments:
    def test_monthly_generates_full_year(self, elapsed_year_enrollment, parent):
        created = PaymentService.schedule_academic_year_payments(elapsed_year_enrollment, parent)
        assert created == 10  # Sep–Jun inclusive

        monthly = Payment.objects.filter(enrollment=elapsed_year_enrollment, payment_type="monthly")
        assert monthly.count() == 10
        assert all(p.payment_status == "pending" for p in monthly)

        due = sorted(monthly.values_list("due_date", flat=True))
        assert due[0] == date(2025, 9, 30)  # due at month end
        assert due[-1] == date(2026, 6, 30)

    def test_idempotent(self, elapsed_year_enrollment, parent):
        PaymentService.schedule_academic_year_payments(elapsed_year_enrollment, parent)
        second = PaymentService.schedule_academic_year_payments(elapsed_year_enrollment, parent)
        assert second == 0
        assert Payment.objects.filter(enrollment=elapsed_year_enrollment, payment_type="monthly").count() == 10

    def test_quarterly_anchors_to_the_enrollment_month(
        self, student, enrollment_type_returning_student, site_config, parent
    ):
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
        # Anchored to September: Sep-Nov, Dec-Feb, Mar-May, then a one-month June
        # stub because the academic year ends first. The old fixed Oct/Jan/Apr
        # calendar produced three and left SEPTEMBER unbilled entirely.
        assert created == 4
        q = Payment.objects.filter(enrollment=enrollment, payment_type="quarterly")
        assert sorted(p.due_date.month for p in q) == [2, 5, 6, 11]

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
        assert created == 4
        # The agreed price is per QUARTER, so the one-month June stub is a third of it.
        assert set(Payment.objects.filter(enrollment=enrollment).values_list("amount", flat=True)) == {
            Decimal("120.00"),
            Decimal("40.00"),
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


class TestMidYearEnrollment:
    """The scenarios that the fixed Oct/Jan/Apr calendar got wrong.

    Classes start ~15 Sep. A student joining part-way through a month pays only
    the remaining days OF THAT MONTH; every later month is full.
    """

    @staticmethod
    def _enrollment(student, etype, modality, joined):
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

    def test_monthly_joiner_is_prorated_for_the_first_month_only(
        self, student, enrollment_type_returning_student, site_config, parent
    ):
        e = self._enrollment(student, enrollment_type_returning_student, "monthly", date(2025, 12, 12))
        PaymentService.schedule_academic_year_payments(e, parent, as_of=date(2026, 6, 30))

        rows = {p.due_date: p.amount for p in Payment.objects.filter(enrollment=e, payment_type="monthly")}
        # 12 Dec on a 31-day month = 20/31 of the fee, and December IS billed.
        assert rows[date(2025, 12, 31)] == (site_config.full_time_monthly_fee * Decimal(20) / Decimal(31)).quantize(
            Decimal("0.01")
        )
        # Every later month is full.
        assert rows[date(2026, 1, 31)] == site_config.full_time_monthly_fee
        assert rows[date(2026, 2, 28)] == site_config.full_time_monthly_fee

    def test_september_start_bills_roughly_half_that_month(
        self, student, enrollment_type_returning_student, site_config, parent
    ):
        """15 Sep on a 30-day month = 16/30 — the half month falls out of proration."""
        e = self._enrollment(student, enrollment_type_returning_student, "monthly", date(2025, 9, 15))
        PaymentService.schedule_academic_year_payments(e, parent, as_of=date(2026, 6, 30))

        september = Payment.objects.get(enrollment=e, due_date=date(2025, 9, 30))
        assert september.amount == (site_config.full_time_monthly_fee * Decimal(16) / Decimal(30)).quantize(
            Decimal("0.01")
        )
        assert "parcial" in september.concept
        october = Payment.objects.get(enrollment=e, due_date=date(2025, 10, 31))
        assert october.amount == site_config.full_time_monthly_fee

    def test_quarterly_joiner_is_billed_from_the_month_they_joined(
        self, student, enrollment_type_returning_student, site_config, parent
    ):
        """The December gap: fixed quarters left a 12-Dec joiner unbilled for December."""
        e = self._enrollment(student, enrollment_type_returning_student, "quarterly", date(2025, 12, 12))
        PaymentService.schedule_academic_year_payments(e, parent, as_of=date(2026, 6, 30))

        due = sorted(p.due_date for p in Payment.objects.filter(enrollment=e, payment_type="quarterly"))
        # Dec-Feb, Mar-May, then the June stub — due on the LAST day of each block.
        assert due == [date(2026, 2, 28), date(2026, 5, 31), date(2026, 6, 30)]
        first = Payment.objects.get(enrollment=e, due_date=date(2026, 2, 28))
        assert "Diciembre-Febrero" in first.concept
        assert "parcial" in first.concept

    def test_only_the_open_period_is_created_then_celery_adds_the_rest(
        self, student, enrollment_type_returning_student, site_config, parent
    ):
        e = self._enrollment(student, enrollment_type_returning_student, "monthly", date(2025, 12, 12))

        created = PaymentService.schedule_academic_year_payments(e, parent, as_of=date(2025, 12, 12))
        assert created == 1  # only December has opened

        # 1 January: the cron opens January and nothing else.
        assert PaymentService.schedule_academic_year_payments(e, parent, as_of=date(2026, 1, 1)) == 1
        assert Payment.objects.filter(enrollment=e, payment_type="monthly").count() == 2

    def test_a_missed_run_is_backfilled(self, student, enrollment_type_returning_student, site_config, parent):
        """A month the scheduler missed is repaired on the next run, not lost."""
        e = self._enrollment(student, enrollment_type_returning_student, "monthly", date(2025, 12, 12))
        PaymentService.schedule_academic_year_payments(e, parent, as_of=date(2025, 12, 12))

        # Jan and Feb never ran; March picks all three up.
        assert PaymentService.schedule_academic_year_payments(e, parent, as_of=date(2026, 3, 1)) == 3
        assert Payment.objects.filter(enrollment=e, payment_type="monthly").count() == 4


class TestJuneEnrollmentRollsToNextYear:
    def test_student_created_in_june_is_billed_from_september(
        self, student, enrollment_type_returning_student, site_config, parent
    ):
        """Enrolment rolls over in May, so a June signup joins the NEXT course."""
        from billing.models import current_academic_year

        assert current_academic_year(date(2026, 6, 10)) == "2026-2027"

        e = Enrollment.objects.create(
            student=student,
            enrollment_type=enrollment_type_returning_student,
            enrollment_period_start=date(2026, 9, 14),
            enrollment_period_end=date(2027, 6, 25),
            academic_year="2026-2027",
            schedule_type="full_time",
            payment_modality="monthly",
            enrollment_amount=Decimal("54.00"),
            discount_percentage=Decimal("0.00"),
            final_amount=Decimal("54.00"),
            status="active",
            enrollment_date=date(2026, 6, 10),
        )
        PaymentService.schedule_academic_year_payments(e, parent, as_of=date(2026, 6, 10))

        # The first fee is issued immediately even though September is months away,
        # and it is NOT prorated — they join before the course starts.
        rows = Payment.objects.filter(enrollment=e, payment_type="monthly")
        assert rows.count() == 1
        september = rows.get()
        assert september.due_date == date(2026, 9, 30)
        assert september.amount == site_config.full_time_monthly_fee
        assert "parcial" not in september.concept
