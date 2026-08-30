"""Tests for the EnrollmentType provisioning service and its management command."""

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
        assert labels["monthly"] == "Mensual"
        assert labels["quarterly"] == "Trimestral"
        assert labels["adults"] == "Adultos"
        assert labels["special"] == "Especial"

    def test_amounts_come_from_site_configuration(self):
        EnrollmentType.objects.all().delete()
        config = SiteConfiguration.get_config()

        ensure_enrollment_types(config)

        monthly = EnrollmentType.objects.get(name="monthly")
        assert monthly.base_amount_full_time == config.full_time_monthly_fee
        assert monthly.base_amount_part_time == config.part_time_monthly_fee

        quarterly = EnrollmentType.objects.get(name="quarterly")
        assert quarterly.base_amount_full_time == config.full_time_monthly_fee * 3

        adults = EnrollmentType.objects.get(name="adults")
        assert adults.base_amount_full_time == config.adult_group_monthly_fee
        assert adults.base_amount_part_time == config.adult_group_monthly_fee

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
        EnrollmentType.objects.filter(name="monthly").update(display_name="Monthly")

        report = ensure_enrollment_types()

        assert any("monthly" in entry for entry in report["updated"])
        assert EnrollmentType.objects.get(name="monthly").display_name == "Mensual"

    def test_repairs_a_drifted_amount(self):
        EnrollmentType.objects.all().delete()
        ensure_enrollment_types()
        EnrollmentType.objects.filter(name="monthly").update(base_amount_full_time=Decimal("1.00"))

        ensure_enrollment_types()

        config = SiteConfiguration.get_config()
        assert EnrollmentType.objects.get(name="monthly").base_amount_full_time == config.full_time_monthly_fee

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

    def test_resolve_plan_raises_on_an_empty_table(self):
        EnrollmentType.objects.all().delete()
        config = SiteConfiguration.get_config()

        with pytest.raises(ValueError, match="EnrollmentType 'monthly' not found"):
            EnrollmentService._resolve_plan(config, {"enrollment_plan": "monthly_full"}, False, False, None)

    @pytest.mark.parametrize(
        ("is_adult", "is_special", "manual", "plan", "expected"),
        [
            (False, False, None, "monthly_full", "monthly"),
            (False, False, None, "monthly_part", "monthly"),
            (False, False, None, "quarterly", "quarterly"),
            (True, False, None, "monthly_full", "adults"),
            (True, True, Decimal("50.00"), "monthly_full", "special"),
            (False, True, Decimal("50.00"), "monthly_full", "special"),
        ],
    )
    def test_every_plan_resolves_after_seeding(self, is_adult, is_special, manual, plan, expected):
        EnrollmentType.objects.all().delete()
        ensure_enrollment_types()
        config = SiteConfiguration.get_config()

        enrollment_type, _, _, _ = EnrollmentService._resolve_plan(
            config, {"enrollment_plan": plan}, is_adult, is_special, manual
        )

        assert enrollment_type.name == expected
