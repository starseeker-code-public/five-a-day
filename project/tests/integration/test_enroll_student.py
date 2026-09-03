"""Tests for the "Nueva matrícula" flow — the book icon on the student list
(`enroll_student` AJAX endpoint) and the start-date leg of student creation.

Explicit dates use an ELAPSED academic year (2020-2021) so every billing period
has started and counts/amounts hold on any run date — the same anchoring
rationale as `elapsed_year_enrollment` in test_payment_scheduling.py.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest
from django.urls import reverse

from billing.models import Enrollment, Payment
from students.models import Student

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _allow_elapsed_course_start_dates(monkeypatch):
    """These tests anchor to the ELAPSED 2020-2021 course (see module docstring).

    `EnrollmentForm.clean_start_date` bounds new start dates to the courses
    currently in play (a typo-year guard), which would reject the anchor —
    widen the window to include it. The window rule itself is covered in
    unit/test_enrollment_start_date.py.
    """
    from billing.models import relevant_academic_years as _real

    monkeypatch.setattr(
        "billing.models.relevant_academic_years",
        lambda reference_date=None: ["2020-2021", *_real(reference_date)],
    )


class TestEnrollStudentEndpoint:
    def test_finishes_current_enrollment_and_bills_from_start_date(
        self,
        authenticated_client,
        student_with_parent,
        parent,
        active_enrollment,
        enrollment_type_returning_student,
        site_config,
    ):
        response = authenticated_client.post(
            reverse("enroll_student", args=[student_with_parent.id]),
            {"enrollment_plan": "monthly_full", "start_date": "2020-11-15", "charge_enrollment_fee": "on"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["academic_year"] == "2020-2021"

        # The previous active enrollment is superseded, not left violating the
        # one-active-enrollment-per-student constraint.
        active_enrollment.refresh_from_db()
        assert active_enrollment.status == "finished"

        new_enrollment = Enrollment.objects.get(id=data["enrollment_id"])
        assert new_enrollment.status == "active"
        assert new_enrollment.enrollment_date == date(2020, 11, 15)
        # An enrollment for another year exists → returning matrícula category.
        assert new_enrollment.enrollment_type.name == "returning_student"

        # Matrícula: due the last day of the START month, discounted for the
        # returning student, owned by the titular parent.
        fee_payment = Payment.objects.get(student=student_with_parent, payment_type="enrollment")
        assert fee_payment.due_date == date(2020, 11, 30)
        expected_fee = site_config.children_enrollment_fee - site_config.returning_student_enrollment_discount
        assert fee_payment.amount == expected_fee
        assert fee_payment.parent == parent

        # Periodic payments start at the start date — first one prorated 16/30,
        # nothing due before November.
        assert data["payments_created"] == 8  # Nov..Jun, year fully elapsed
        monthlies = list(
            Payment.objects.filter(student=student_with_parent, payment_type="monthly").order_by("due_date")
        )
        assert monthlies[0].due_date == date(2020, 11, 30)
        expected_first = (new_enrollment.final_amount * Decimal(16) / Decimal(30)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        assert monthlies[0].amount == expected_first
        assert monthlies[0].parent == parent

    def test_charge_fee_unchecked_skips_matricula(
        self,
        authenticated_client,
        student_with_parent,
        active_enrollment,
        enrollment_type_returning_student,
        site_config,
    ):
        response = authenticated_client.post(
            reverse("enroll_student", args=[student_with_parent.id]),
            {"enrollment_plan": "monthly_full", "start_date": "2020-11-01"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert not Payment.objects.filter(student=student_with_parent, payment_type="enrollment").exists()
        assert Payment.objects.filter(student=student_with_parent, payment_type="monthly").exists()

    def test_adult_student_enrolls_without_parent(
        self, authenticated_client, adult_student, enrollment_type_adults, site_config
    ):
        response = authenticated_client.post(
            reverse("enroll_student", args=[adult_student.id]),
            {"start_date": "2020-11-01", "charge_enrollment_fee": "on"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

        enrollment = adult_student.enrollments.get()
        assert enrollment.schedule_type == "adult_group"
        # Adults legitimately have no parent/guardian on their payments.
        assert all(p.parent is None for p in adult_student.payments.all())
        fee_payment = adult_student.payments.get(payment_type="enrollment")
        assert fee_payment.amount == site_config.adult_enrollment_fee
        assert fee_payment.due_date == date(2020, 11, 30)

    def test_invalid_form_returns_400_and_changes_nothing(
        self, authenticated_client, student_with_parent, active_enrollment, site_config
    ):
        """Precio especial without a manual amount is the form's own error —
        validation fails BEFORE the current enrollment is touched."""
        response = authenticated_client.post(
            reverse("enroll_student", args=[student_with_parent.id]),
            {"enrollment_plan": "monthly_full", "is_special": "on"},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"]

        active_enrollment.refresh_from_db()
        assert active_enrollment.status == "active"
        assert student_with_parent.enrollments.count() == 1

    def test_get_method_not_allowed(self, authenticated_client, student):
        response = authenticated_client.get(reverse("enroll_student", args=[student.id]))
        assert response.status_code == 405

    def test_inactive_student_404(self, authenticated_client, inactive_student, site_config):
        response = authenticated_client.post(
            reverse("enroll_student", args=[inactive_student.id]),
            {"enrollment_plan": "monthly_full"},
        )
        assert response.status_code == 404


class TestStudentCreateWithStartDate:
    def test_student_created_now_starting_later_bills_from_start(
        self, authenticated_client, parent, group, site_config, enrollment_type_new_student
    ):
        """The ficha is created today but the student starts 15 Nov 2020: the
        academic year, the matrícula due date and every periodic payment follow
        the START date, and the brand-new student's own enrollment must not
        read as prior history (full matrícula, `new_student` category)."""
        response = authenticated_client.post(
            reverse("student_create") + f"?parent_id={parent.id}",
            {
                "first_name": "Tardio",
                "last_name": "Alumno",
                "birth_date": "2014-03-10",
                "school": "CEIP Nuevo",
                "gdpr_signed": "on",
                "group": group.id,
                "parent_id": parent.id,
                "enrollment_plan": "monthly_full",
                "start_date": "2020-11-15",
            },
        )
        assert response.status_code == 302

        student = Student.objects.get(first_name="Tardio")
        enrollment = student.enrollments.get()
        assert enrollment.enrollment_date == date(2020, 11, 15)
        assert enrollment.academic_year == "2020-2021"
        assert enrollment.enrollment_type.name == "new_student"

        fee_payment = student.payments.get(payment_type="enrollment")
        assert fee_payment.amount == site_config.children_enrollment_fee
        assert fee_payment.due_date == date(2020, 11, 30)

        monthlies = list(student.payments.filter(payment_type="monthly").order_by("due_date"))
        assert monthlies[0].due_date == date(2020, 11, 30)
        billed = {(p.due_date.month, p.due_date.year) for p in monthlies}
        assert (9, 2020) not in billed
        assert (10, 2020) not in billed
