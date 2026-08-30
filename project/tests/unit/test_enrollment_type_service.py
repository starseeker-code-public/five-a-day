"""Tests for the EnrollmentType provisioning service and its management command."""

from datetime import date
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command

from billing.models import EnrollmentType, SiteConfiguration
from billing.services.enrollment_service import EnrollmentService
from billing.services.enrollment_type_service import (
    REQUIRED_ENROLLMENT_TYPES,
    ensure_enrollment_types,
)

pytestmark = pytest.mark.django_db


class TestEnsureEnrollmentTypes:
    def test_creates_every_required_type(self):
        EnrollmentType.objects.all().delete()

        report = ensure_enrollment_types()

        assert set(report["created"]) == set(REQUIRED_ENROLLMENT_TYPES)
        assert EnrollmentType.objects.count() == len(REQUIRED_ENROLLMENT_TYPES)

    def test_includes_special_which_seed_testdata_used_to_omit(self):
        EnrollmentType.objects.all().delete()
        ensure_enrollment_types()
        # A special enrollment resolves this row; without it enrollment raised.
        assert EnrollmentType.objects.filter(name="special").exists()

    def test_display_names_are_spanish(self):
        EnrollmentType.objects.all().delete()
        ensure_enrollment_types()

        labels = dict(EnrollmentType.objects.values_list("name", "display_name"))
        assert labels["new_student"] == "Nuevo estudiante"
        assert labels["returning_student"] == "Antiguo estudiante"
        assert labels["adults"] == "Adulto"
        assert labels["special"] == "Especial"

    def test_amounts_are_the_matricula_fees_from_site_configuration(self):
        EnrollmentType.objects.all().delete()
        config = SiteConfiguration.get_config()

        ensure_enrollment_types(config)

        new_student = EnrollmentType.objects.get(name="new_student")
        assert new_student.base_amount_full_time == config.children_enrollment_fee
        # A matrícula does not vary with the schedule, so both columns agree.
        assert new_student.base_amount_part_time == config.children_enrollment_fee

        returning = EnrollmentType.objects.get(name="returning_student")
        assert returning.base_amount_full_time == (
            config.children_enrollment_fee - config.returning_student_enrollment_discount
        )

        adults = EnrollmentType.objects.get(name="adults")
        assert adults.base_amount_full_time == config.adult_enrollment_fee
        assert adults.base_amount_part_time == config.adult_enrollment_fee

    def test_is_idempotent(self):
        EnrollmentType.objects.all().delete()
        ensure_enrollment_types()

        second = ensure_enrollment_types()

        assert second["created"] == []
        assert second["updated"] == []
        assert set(second["unchanged"]) == set(REQUIRED_ENROLLMENT_TYPES)
        assert EnrollmentType.objects.count() == len(REQUIRED_ENROLLMENT_TYPES)

    def test_repairs_an_english_display_name(self):
        EnrollmentType.objects.all().delete()
        ensure_enrollment_types()
        EnrollmentType.objects.filter(name="new_student").update(display_name="New Student")

        report = ensure_enrollment_types()

        assert any("new_student" in entry for entry in report["updated"])
        assert EnrollmentType.objects.get(name="new_student").display_name == "Nuevo estudiante"

    def test_repairs_a_drifted_amount(self):
        EnrollmentType.objects.all().delete()
        ensure_enrollment_types()
        EnrollmentType.objects.filter(name="new_student").update(base_amount_full_time=Decimal("1.00"))

        ensure_enrollment_types()

        config = SiteConfiguration.get_config()
        assert EnrollmentType.objects.get(name="new_student").base_amount_full_time == config.children_enrollment_fee

    def test_does_not_clobber_admin_edited_description_or_active(self):
        EnrollmentType.objects.all().delete()
        ensure_enrollment_types()
        EnrollmentType.objects.filter(name="adults").update(description="Nota interna", active=False)

        ensure_enrollment_types()

        adults = EnrollmentType.objects.get(name="adults")
        assert adults.description == "Nota interna"
        assert adults.active is False


class TestSeedEnrollmentTypesCommand:
    def test_command_provisions_an_empty_table(self):
        EnrollmentType.objects.all().delete()
        out = StringIO()

        call_command("seed_enrollment_types", stdout=out)

        assert EnrollmentType.objects.count() == len(REQUIRED_ENROLLMENT_TYPES)
        assert "4 created" in out.getvalue()

    def test_command_is_safe_to_rerun(self):
        EnrollmentType.objects.all().delete()
        call_command("seed_enrollment_types", stdout=StringIO())
        out = StringIO()

        call_command("seed_enrollment_types", stdout=out)

        assert "0 created" in out.getvalue()
        assert EnrollmentType.objects.count() == len(REQUIRED_ENROLLMENT_TYPES)


class TestEnrollmentUnblocked:
    """The regression this whole change exists to prevent."""

    def test_resolve_type_raises_on_an_empty_table(self, student):
        EnrollmentType.objects.all().delete()

        with pytest.raises(ValueError, match="EnrollmentType 'new_student' not found"):
            EnrollmentService._resolve_enrollment_type(student, False, False, None)

    @pytest.mark.parametrize(
        ("is_adult", "is_special", "manual", "expected"),
        [
            (False, False, None, "new_student"),
            (True, False, None, "adults"),
            (True, True, Decimal("50.00"), "special"),
            (False, True, Decimal("50.00"), "special"),
        ],
    )
    def test_every_category_resolves_after_seeding(self, student, is_adult, is_special, manual, expected):
        EnrollmentType.objects.all().delete()
        ensure_enrollment_types()

        enrollment_type = EnrollmentService._resolve_enrollment_type(student, is_adult, is_special, manual)

        assert enrollment_type.name == expected

    @pytest.mark.parametrize("plan", ["monthly_full", "monthly_part", "quarterly"])
    def test_the_category_does_not_depend_on_the_payment_plan(self, student, plan):
        """Monthly vs quarterly is `payment_modality`, never a kind of matrícula."""
        EnrollmentType.objects.all().delete()
        ensure_enrollment_types()

        enrollment = EnrollmentService.create_enrollment(student, {"enrollment_plan": plan}, is_adult=False)

        assert enrollment.enrollment_type.name == "new_student"

    def test_re_enrolling_in_a_later_year_resolves_to_returning_student(self, student):
        EnrollmentType.objects.all().delete()
        ensure_enrollment_types()
        EnrollmentService.create_enrollment(student, {"enrollment_plan": "monthly_full"}, is_adult=False)
        student.enrollments.update(academic_year="2000-2001", status="finished")

        enrollment = EnrollmentService.create_enrollment(student, {"enrollment_plan": "monthly_full"}, is_adult=False)

        assert enrollment.enrollment_type.name == "returning_student"


class TestEnrolmentYearRollover:
    """A student enrolled between May and August joins the NEXT course.

    Before the May rollover, `current_academic_year()` returned the course whose
    classes had already finished, so `create_enrollment` set an
    `enrollment_period_start` in the past and `schedule_academic_year_payments`
    generated a whole year of fees against months that had already gone by.
    """

    def test_august_enrolment_starts_in_the_coming_september(self, student):
        from datetime import date
        from unittest.mock import patch

        from billing.models import academic_year_end_date, academic_year_start_date

        ensure_enrollment_types()

        with patch("billing.services.enrollment_service.date") as mock_date:
            mock_date.today.return_value = date(2026, 8, 30)
            enrollment = EnrollmentService.create_enrollment(
                student, {"enrollment_plan": "monthly_full"}, is_adult=False
            )

        assert enrollment.academic_year == "2026-2027"
        # Classes start in the September AFTER the signup, never before it.
        assert enrollment.enrollment_period_start == academic_year_start_date(2026)
        assert enrollment.enrollment_period_start > date(2026, 8, 30)
        assert enrollment.enrollment_period_end == academic_year_end_date(2027)


class TestCategoryDataMigration:
    """`billing/migrations/0008` re-points enrollments off the retired cadence types.

    The dev and production databases both had zero enrollments when it was written, so
    the interesting branch — a live enrollment sitting on `monthly` or `quarterly` — is
    only covered here. The migration functions take the real app registry, and
    `Model.objects.create()` does not enforce `choices`, so a retired name can be staged.
    """

    @staticmethod
    def _migration():
        import importlib

        return importlib.import_module("billing.migrations.0008_enrollment_type_categories")

    @staticmethod
    def _stale_type(name):
        return EnrollmentType.objects.create(
            name=name,
            display_name=name.title(),
            base_amount_full_time=Decimal("54.00"),
            base_amount_part_time=Decimal("36.00"),
        )

    @staticmethod
    def _enrollment(student, enrollment_type, academic_year, schedule_type="full_time", status="active"):
        from billing.models import Enrollment

        return Enrollment.objects.create(
            student=student,
            enrollment_type=enrollment_type,
            enrollment_period_start=date(2025, 9, 15),
            enrollment_period_end=date(2026, 6, 26),
            academic_year=academic_year,
            schedule_type=schedule_type,
            payment_modality="monthly",
            enrollment_amount=Decimal("54.00"),
            final_amount=Decimal("54.00"),
            status=status,
            enrollment_date=date(2025, 9, 1),
        )

    def _run(self):
        from django.apps import apps

        self._migration().migrate_types(apps, None)

    def test_a_first_time_enrollment_becomes_new_student(self, student, site_config):
        EnrollmentType.objects.all().delete()
        enrollment = self._enrollment(student, self._stale_type("monthly"), "2025-2026")

        self._run()

        enrollment.refresh_from_db()
        assert enrollment.enrollment_type.name == "new_student"

    def test_an_earlier_academic_year_makes_it_returning(self, student, site_config):
        EnrollmentType.objects.all().delete()
        stale = self._stale_type("quarterly")
        # `unique_active_enrollment_per_student` allows only one live row.
        self._enrollment(student, stale, "2024-2025", status="finished")
        enrollment = self._enrollment(student, stale, "2025-2026")

        self._run()

        enrollment.refresh_from_db()
        assert enrollment.enrollment_type.name == "returning_student"

    def test_an_adult_schedule_becomes_adults(self, student, site_config):
        EnrollmentType.objects.all().delete()
        enrollment = self._enrollment(student, self._stale_type("monthly"), "2025-2026", schedule_type="adult_group")

        self._run()

        enrollment.refresh_from_db()
        assert enrollment.enrollment_type.name == "adults"

    def test_retired_rows_are_dropped_and_amounts_become_matricula_fees(self, student, site_config):
        EnrollmentType.objects.all().delete()
        self._enrollment(student, self._stale_type("monthly"), "2025-2026")

        self._run()

        assert set(EnrollmentType.objects.values_list("name", flat=True)) == set(REQUIRED_ENROLLMENT_TYPES)
        assert EnrollmentType.objects.get(name="new_student").base_amount_full_time == (
            site_config.children_enrollment_fee
        )

    def test_does_nothing_on_a_fresh_empty_table(self, site_config):
        """`seed_enrollment_types` stays the only path that provisions reference data."""
        EnrollmentType.objects.all().delete()

        self._run()

        assert EnrollmentType.objects.count() == 0
