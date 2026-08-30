"""Tests for the returning-student enrollment discount (v1.13)."""

from datetime import date
from decimal import Decimal

import pytest

from billing.models import Enrollment, SiteConfiguration
from billing.services.enrollment_service import EnrollmentService

pytestmark = pytest.mark.django_db


class TestIsReturningStudent:
    def test_no_prior_enrollments_returns_false(self, student):
        assert EnrollmentService.is_returning_student(student) is False

    def test_only_current_year_enrollment_returns_false(self, student, active_enrollment):
        """The student's only Enrollment is for this year (i.e. the one we're
        creating right now) → not "returning"."""
        current_year = active_enrollment.academic_year
        assert EnrollmentService.is_returning_student(student, current_year) is False

    def test_prior_year_enrollment_marks_returning(self, student, enrollment_type_new_student, active_enrollment):
        """Add a prior-year Enrollment → student is returning."""
        Enrollment.objects.create(
            student=student,
            enrollment_type=enrollment_type_new_student,
            enrollment_period_start=date(2023, 9, 15),
            enrollment_period_end=date(2024, 6, 27),
            academic_year="2023-2024",
            schedule_type="full_time",
            payment_modality="monthly",
            enrollment_amount=Decimal("54.00"),
            final_amount=Decimal("54.00"),
            status="finished",
            enrollment_date=date(2023, 9, 1),
        )
        assert EnrollmentService.is_returning_student(student, "2025-2026") is True

    def test_cancelled_prior_enrollment_still_counts(self, student, cancelled_enrollment):
        """A cancelled prior-year enrollment still marks the student as
        returning — they were signed up once, that's what matters."""
        current_year = "2025-2026"  # cancelled_enrollment is 2024-2025
        assert EnrollmentService.is_returning_student(student, current_year) is True


class TestComputeEnrollmentFee:
    def test_new_child_pays_full_fee(self, student, site_config):
        fee, discount = EnrollmentService.compute_enrollment_fee(site_config, student, is_adult=False)
        assert fee == site_config.children_enrollment_fee
        assert discount == Decimal("0.00")

    def test_returning_child_gets_discount(self, student, cancelled_enrollment, site_config):
        """cancelled_enrollment is for academic_year 2024-2025 → prior year."""
        fee, discount = EnrollmentService.compute_enrollment_fee(site_config, student, is_adult=False)
        expected_discount = site_config.returning_student_enrollment_discount
        assert discount == expected_discount
        assert fee == site_config.children_enrollment_fee - expected_discount

    def test_adult_never_gets_returning_discount(self, adult_student, cancelled_enrollment, site_config):
        """Adults have their own enrollment fee and don't participate in
        the returning-student discount."""
        cancelled_enrollment.student = adult_student
        cancelled_enrollment.save()
        fee, discount = EnrollmentService.compute_enrollment_fee(site_config, adult_student, is_adult=True)
        assert fee == site_config.adult_enrollment_fee
        assert discount == Decimal("0.00")

    def test_zero_configured_discount_is_noop(self, student, cancelled_enrollment, site_config):
        site_config.returning_student_enrollment_discount = Decimal("0.00")
        site_config.save()
        fee, discount = EnrollmentService.compute_enrollment_fee(site_config, student, is_adult=False)
        assert discount == Decimal("0.00")
        assert fee == site_config.children_enrollment_fee

    def test_discount_never_pushes_fee_below_zero(self, student, cancelled_enrollment, site_config):
        """If someone configures a huge discount, the fee floors at 0."""
        site_config.returning_student_enrollment_discount = Decimal("9999.00")
        site_config.save()
        fee, _ = EnrollmentService.compute_enrollment_fee(site_config, student, is_adult=False)
        assert fee == Decimal("0.00")


class TestSiteConfigurationDefaults:
    def test_default_discount_is_20_euros(self):
        SiteConfiguration.objects.all().delete()
        config = SiteConfiguration.get_config()
        assert config.returning_student_enrollment_discount == Decimal("20.00")


class TestUpdateSiteConfigApi:
    def test_admin_can_update_discount(self, authenticated_client, site_config):
        import json

        from django.urls import reverse

        response = authenticated_client.post(
            reverse("update_site_config"),
            data=json.dumps({"returning_student_enrollment_discount": "35.00"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        site_config.refresh_from_db()
        assert site_config.returning_student_enrollment_discount == Decimal("35.00")
