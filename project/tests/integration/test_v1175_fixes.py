"""Regression locks for the v1.17.5 round of fixes.

One class per reported problem so a revert fails loudly and names the symptom.
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from billing.models import Expense

pytestmark = pytest.mark.django_db


class TestSpanishChoiceLabels:
    """The payment detail page showed "Monthly Fee" in English."""

    def test_payment_type_labels_are_spanish(self, pending_payment):
        pending_payment.payment_type = "monthly"
        pending_payment.save()
        assert pending_payment.get_payment_type_display() == "Mensualidad"

    def test_payment_status_labels_are_spanish(self, pending_payment):
        assert pending_payment.get_payment_status_display() == "Pendiente"

    def test_enrollment_status_labels_are_spanish(self, active_enrollment):
        assert active_enrollment.get_status_display() == "Activa"

    @pytest.mark.parametrize(
        ("key", "label"),
        [
            ("enrollment", "Matrícula"),
            ("monthly", "Mensualidad"),
            ("quarterly", "Trimestre"),
            ("other", "Otro"),
        ],
    )
    def test_every_payment_type_is_translated(self, key, label):
        from billing import constants

        assert dict(constants.PAYMENT_TYPE_CHOICES)[key] == label


class TestEmailTemplatesHaveARealTitle:
    """Every one of these inherited the generic "Five a Day" title."""

    TEMPLATES = [
        "enrollment_adult",
        "enrollment_child",
        "fun_friday",
        "happy_birthday",
        "newsletter",
        "payment_reminder",
        "receipt_adult",
        "receipt_enrollment",
        "receipt_quarterly_child",
        "tax_certificate",
        "vacation_closure",
    ]

    @pytest.mark.parametrize("name", TEMPLATES)
    def test_template_defines_its_own_title(self, name):
        from pathlib import Path

        from django.conf import settings

        source = Path(settings.BASE_DIR, "core", "templates", "emails", f"{name}.html").read_text(encoding="utf-8")
        assert "{% block title %}" in source, f"{name} falls back to the generic title"


class TestDatesAreDayMonthYear:
    """DATE_FORMAT was overridden by the es locale, so an unfiltered date
    rendered as "31 de agosto de 2026"."""

    def test_a_bare_date_renders_dd_mm_yyyy(self):
        from django.template import Context, Template

        rendered = Template("{{ d }}").render(Context({"d": date(2026, 8, 31)}))
        assert rendered == "31/08/2026"

    def test_short_date_format_agrees(self):
        from django.template import Context, Template

        rendered = Template('{{ d|date:"SHORT_DATE_FORMAT" }}').render(Context({"d": date(2026, 8, 31)}))
        assert rendered == "31/08/2026"

    def test_format_module_path_is_configured(self):
        from django.conf import settings

        # Without this the DATE_FORMAT settings are silently inert.
        assert settings.FORMAT_MODULE_PATH == "project.formats"


class TestPaymentStudentSearchCarriesTheParent:
    """The parent box stayed empty because the second request that filled it
    swallowed every error."""

    def test_search_returns_the_parent(self, authenticated_client, student_with_parent, parent):
        response = authenticated_client.get(reverse("search_students"), {"q": student_with_parent.first_name[:3]})

        result = next(r for r in response.json()["results"] if r["id"] == student_with_parent.id)
        assert result["parent_id"] == parent.id
        assert result["parent_name"] == parent.full_name

    def test_adult_student_reports_no_parent_rather_than_failing(self, authenticated_client, adult_student):
        response = authenticated_client.get(reverse("search_students"), {"q": adult_student.first_name[:3]})

        result = next(r for r in response.json()["results"] if r["id"] == adult_student.id)
        assert result["parent_id"] is None
        assert result["parent_name"] == ""


class TestExpenseUpdate:
    """There was no update path at all — the rent could only be deleted and rebuilt."""

    @pytest.fixture
    def rent_template(self):
        return Expense.objects.create(
            description="Alquiler local",
            category="rent",
            amount=Decimal("800.00"),
            expense_date=date(2026, 1, 1),
            is_recurring=True,
            recurring_frequency="monthly",
            recurring_day=1,
        )

    def _post(self, client, expense, **overrides):
        data = {
            "description": expense.description,
            "category": expense.category,
            "amount": str(expense.amount),
            "expense_date": expense.expense_date.isoformat(),
            "notes": "",
            "is_recurring": "on" if expense.is_recurring else "",
            "recurring_frequency": expense.recurring_frequency,
            "recurring_day": expense.recurring_day or "",
        }
        data.update(overrides)
        return client.post(reverse("update_expense", args=[expense.id]), data)

    def test_the_rent_amount_can_be_raised(self, authenticated_client, rent_template):
        response = self._post(authenticated_client, rent_template, amount="850.00")

        assert response.status_code == 302
        rent_template.refresh_from_db()
        assert rent_template.amount == Decimal("850.00")

    def test_the_cadence_can_be_changed(self, authenticated_client, rent_template):
        self._post(authenticated_client, rent_template, recurring_day="28")

        rent_template.refresh_from_db()
        assert rent_template.recurring_day == 28

    def test_already_generated_rows_are_left_alone(self, authenticated_client, rent_template):
        generated = Expense.objects.create(
            description="Alquiler local",
            category="rent",
            amount=Decimal("800.00"),
            expense_date=date(2026, 2, 1),
            generated_from=rent_template,
        )

        self._post(authenticated_client, rent_template, amount="850.00")

        generated.refresh_from_db()
        # What was actually paid in February must not be rewritten.
        assert generated.amount == Decimal("800.00")
        assert generated.generated_from_id == rent_template.id

    def test_a_weekly_edit_without_weekdays_is_rejected(self, authenticated_client, rent_template):
        self._post(authenticated_client, rent_template, recurring_frequency="weekly")

        rent_template.refresh_from_db()
        assert rent_template.recurring_frequency == "monthly"

    def test_an_unknown_category_falls_back_instead_of_persisting(self, authenticated_client, rent_template):
        self._post(authenticated_client, rent_template, category="not-a-category")

        rent_template.refresh_from_db()
        assert rent_template.category == "other"

    def test_a_zero_amount_is_rejected(self, authenticated_client, rent_template):
        self._post(authenticated_client, rent_template, amount="0")

        rent_template.refresh_from_db()
        assert rent_template.amount == Decimal("800.00")


class TestExpenseCreateValidates:
    """`create()` never ran `Expense.clean()`, so an invalid recurrence reached
    the database and then simply never materialised."""

    def test_unknown_category_falls_back_to_other(self, authenticated_client):
        authenticated_client.post(
            reverse("create_expense"),
            {"description": "Café", "category": "wat", "amount": "3.50", "expense_date": "2026-03-01"},
        )

        assert Expense.objects.get(description="Café").category == "other"

    def test_default_expense_date_is_a_real_date(self, authenticated_client):
        response = authenticated_client.get(reverse("expenses_list"), {"month": 3, "year": 2026})

        # <input type="date"> only accepts YYYY-MM-DD; "3-2026" rendered blank.
        assert response.context["default_expense_date"] == date(2026, 3, 1)

    def test_default_expense_date_is_today_for_the_current_month(self, authenticated_client):
        today = date.today()
        response = authenticated_client.get(reverse("expenses_list"), {"month": today.month, "year": today.year})

        assert response.context["default_expense_date"] == today
