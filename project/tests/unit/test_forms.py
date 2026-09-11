"""Tests for billing.forms — EnrollmentForm validation."""

from decimal import Decimal

import pytest

from billing.forms import EnrollmentForm

pytestmark = pytest.mark.django_db


class TestEnrollmentFormValidation:
    def test_valid_monthly_full(self):
        form = EnrollmentForm(data={"enrollment_plan": "monthly_full"})
        assert form.is_valid()

    def test_valid_quarterly(self):
        form = EnrollmentForm(data={"enrollment_plan": "quarterly"})
        assert form.is_valid()

    def test_special_without_anything_fails(self):
        # "Precio especial" with neither a special matrícula nor a custom cuota
        # changes nothing, so it is rejected (v1.28.2: a special can customise
        # the matrícula, the cuota, or both — but must customise SOMETHING).
        form = EnrollmentForm(data={"is_special": True})
        assert not form.is_valid()
        assert "matrícula especial" in str(form.errors)

    def test_special_cuota_requires_the_customize_toggle(self):
        # A bare manual_amount no longer counts: the cuota override is gated on
        # "Personalizar también la cuota" so a stale value can't sneak a hand
        # price onto a special meant to keep the standard cuota.
        form = EnrollmentForm(data={"is_special": True, "manual_amount": "25.00"})
        assert not form.is_valid()

    def test_special_with_custom_cuota_passes(self):
        form = EnrollmentForm(data={"is_special": True, "customize_recurring": True, "manual_amount": "25.00"})
        assert form.is_valid()
        assert form.cleaned_data["manual_amount"] == Decimal("25.00")

    def test_special_matricula_only_passes(self):
        # Custom matrícula, standard cuota — manual_amount stays cleared.
        form = EnrollmentForm(data={"is_special": True, "special_enrollment_fee": "120.00"})
        assert form.is_valid()
        assert form.cleaned_data["manual_amount"] is None

    def test_manual_amount_below_minimum_fails(self):
        form = EnrollmentForm(data={"is_special": True, "customize_recurring": True, "manual_amount": "0.00"})
        assert not form.is_valid()

    def test_sibling_discount_checkbox(self):
        form = EnrollmentForm(data={"enrollment_plan": "monthly_full", "is_sibling_discount": True})
        assert form.is_valid()
        assert form.cleaned_data["is_sibling_discount"] is True

    def test_language_cheque_checkbox(self):
        form = EnrollmentForm(data={"enrollment_plan": "monthly_full", "has_language_cheque": True})
        assert form.is_valid()
        assert form.cleaned_data["has_language_cheque"] is True


class TestEnrollmentFormCreateEnrollment:
    def test_creates_enrollment(self, student, enrollment_type_new_student, site_config):
        form = EnrollmentForm(data={"enrollment_plan": "monthly_full"})
        assert form.is_valid()
        enrollment = form.create_enrollment(student, is_adult=False)
        assert enrollment.student == student
        assert enrollment.status == "active"
        assert enrollment.schedule_type == "full_time"

    def test_creates_adult_enrollment(self, adult_student, enrollment_type_adults, site_config):
        form = EnrollmentForm(data={"enrollment_plan": "monthly_full"})
        assert form.is_valid()
        enrollment = form.create_enrollment(adult_student, is_adult=True)
        assert enrollment.student == adult_student
        assert enrollment.schedule_type == "adult_group"
