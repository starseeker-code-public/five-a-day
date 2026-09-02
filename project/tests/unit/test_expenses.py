"""Unit tests for the Expense model + service (v1.5)."""

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from billing.models import Expense
from billing.services.expense_service import (
    materialize_recurring,
    materialize_recurring_for_date,
    monthly_totals,
)
from core.views.expenses import _parse_amount

pytestmark = pytest.mark.django_db


class TestExpenseModel:
    def test_create_expense(self):
        e = Expense.objects.create(
            description="Alquiler",
            category="rent",
            amount=Decimal("500.00"),
            expense_date=date(2026, 3, 5),
        )
        assert e.pk is not None
        assert str(e).startswith("Alquiler")

    def test_recurring_requires_day(self):
        e = Expense(
            description="Alquiler",
            category="rent",
            amount=Decimal("500.00"),
            expense_date=date(2026, 3, 1),
            is_recurring=True,
        )
        with pytest.raises(ValidationError):
            e.full_clean()

    @pytest.mark.parametrize("day", [0, 32, 99])
    def test_recurring_day_out_of_range(self, day):
        e = Expense(
            description="X",
            category="other",
            amount=Decimal("1.00"),
            expense_date=date(2026, 3, 1),
            is_recurring=True,
            recurring_day=day,
        )
        with pytest.raises(ValidationError):
            e.full_clean()

    @pytest.mark.parametrize("day", [1, 15, 28, 29, 30, 31])
    def test_recurring_day_accepts_the_whole_month(self, day):
        """Days 29-31 are allowed: materialisation clamps them to the month's
        last day, so 31 reads as "el último día de cada mes"."""
        e = Expense(
            description="X",
            category="other",
            amount=Decimal("1.00"),
            expense_date=date(2026, 3, 1),
            is_recurring=True,
            recurring_day=day,
        )
        e.full_clean()  # must not raise

    def test_default_frequency_is_monthly(self):
        e = Expense.objects.create(
            description="Alquiler",
            category="rent",
            amount=Decimal("500.00"),
            expense_date=date(2026, 3, 1),
            is_recurring=True,
            recurring_day=5,
        )
        assert e.recurring_frequency == "monthly"

    def test_yearly_requires_month(self):
        e = Expense(
            description="Seguro anual",
            category="insurance",
            amount=Decimal("800.00"),
            expense_date=date(2026, 3, 1),
            is_recurring=True,
            recurring_frequency="yearly",
            recurring_day=10,
        )
        with pytest.raises(ValidationError):
            e.full_clean()

    def test_yearly_valid(self):
        e = Expense(
            description="Seguro anual",
            category="insurance",
            amount=Decimal("800.00"),
            expense_date=date(2026, 3, 1),
            is_recurring=True,
            recurring_frequency="yearly",
            recurring_day=10,
            recurring_month=6,
        )
        e.full_clean()  # should not raise

    def test_yearly_month_out_of_range(self):
        e = Expense(
            description="X",
            category="other",
            amount=Decimal("1.00"),
            expense_date=date(2026, 3, 1),
            is_recurring=True,
            recurring_frequency="yearly",
            recurring_day=10,
            recurring_month=13,
        )
        with pytest.raises(ValidationError):
            e.full_clean()

    def test_weekly_requires_weekday(self):
        e = Expense(
            description="Limpieza",
            category="other",
            amount=Decimal("30.00"),
            expense_date=date(2026, 3, 1),
            is_recurring=True,
            recurring_frequency="weekly",
        )
        with pytest.raises(ValidationError):
            e.full_clean()

    def test_weekly_valid(self):
        e = Expense(
            description="Limpieza",
            category="other",
            amount=Decimal("30.00"),
            expense_date=date(2026, 3, 1),
            is_recurring=True,
            recurring_frequency="weekly",
            recurring_weekdays="0,2,4",
        )
        e.full_clean()  # should not raise
        assert e.weekday_set() == {0, 2, 4}

    def test_weekly_weekday_out_of_range(self):
        e = Expense(
            description="X",
            category="other",
            amount=Decimal("1.00"),
            expense_date=date(2026, 3, 1),
            is_recurring=True,
            recurring_frequency="weekly",
            recurring_weekdays="7",
        )
        with pytest.raises(ValidationError):
            e.full_clean()


class TestMonthlyTotals:
    def test_empty_month(self):
        totals = monthly_totals(1, 2026)
        assert totals["income"] == Decimal("0.00")
        assert totals["expenses"] == Decimal("0.00")
        assert totals["net"] == Decimal("0.00")
        assert totals["by_category"] == {}

    def test_with_expenses_and_income(self, completed_payment):
        Expense.objects.create(
            description="Alquiler",
            category="rent",
            amount=Decimal("500.00"),
            expense_date=completed_payment.payment_date.replace(day=1),
        )
        Expense.objects.create(
            description="Material",
            category="supplies",
            amount=Decimal("50.00"),
            expense_date=completed_payment.payment_date.replace(day=1),
        )
        totals = monthly_totals(
            completed_payment.payment_date.month,
            completed_payment.payment_date.year,
        )
        assert totals["income"] == completed_payment.amount
        assert totals["expenses"] == Decimal("550.00")
        assert totals["net"] == completed_payment.amount - Decimal("550.00")
        assert totals["by_category"]["rent"] == Decimal("500.00")
        assert totals["by_category"]["supplies"] == Decimal("50.00")

    def test_recurring_templates_excluded(self):
        Expense.objects.create(
            description="Template",
            category="rent",
            amount=Decimal("1000.00"),
            expense_date=date(2026, 3, 1),
            is_recurring=True,
            recurring_day=1,
        )
        totals = monthly_totals(3, 2026)
        assert totals["expenses"] == Decimal("0.00")


class TestMaterializeRecurring:
    def test_creates_one_expense_per_template(self):
        Expense.objects.create(
            description="Alquiler",
            category="rent",
            amount=Decimal("500.00"),
            expense_date=date(2026, 1, 1),
            is_recurring=True,
            recurring_day=5,
        )
        created = materialize_recurring(3, 2026)
        assert created == 1
        materialized = Expense.objects.filter(is_recurring=False, expense_date=date(2026, 3, 5))
        assert materialized.count() == 1
        assert materialized.first().generated_from is not None

    def test_idempotent(self):
        tpl = Expense.objects.create(
            description="Alquiler",
            category="rent",
            amount=Decimal("500.00"),
            expense_date=date(2026, 1, 1),
            is_recurring=True,
            recurring_day=5,
        )
        materialize_recurring(3, 2026)
        second_run = materialize_recurring(3, 2026)
        assert second_run == 0
        assert Expense.objects.filter(generated_from=tpl, expense_date__month=3, expense_date__year=2026).count() == 1

    def test_no_templates_returns_zero(self):
        assert materialize_recurring(3, 2026) == 0

    def test_ignores_non_monthly_frequencies(self):
        Expense.objects.create(
            description="Semanal",
            category="other",
            amount=Decimal("30.00"),
            expense_date=date(2026, 1, 1),
            is_recurring=True,
            recurring_frequency="weekly",
            recurring_weekdays="0",
        )
        Expense.objects.create(
            description="Anual",
            category="insurance",
            amount=Decimal("800.00"),
            expense_date=date(2026, 1, 1),
            is_recurring=True,
            recurring_frequency="yearly",
            recurring_day=1,
            recurring_month=3,
        )
        assert materialize_recurring(3, 2026) == 0


class TestMaterializeRecurringForDate:
    def test_weekly_materialises_on_matching_weekday(self):
        # date(2026, 3, 2) is a Monday (weekday 0).
        tpl = Expense.objects.create(
            description="Limpieza lunes",
            category="other",
            amount=Decimal("30.00"),
            expense_date=date(2026, 1, 1),
            is_recurring=True,
            recurring_frequency="weekly",
            recurring_weekdays="0",
        )
        monday = date(2026, 3, 2)
        assert monday.weekday() == 0
        created = materialize_recurring_for_date(monday)
        assert created == 1
        assert Expense.objects.filter(generated_from=tpl, expense_date=monday, is_recurring=False).count() == 1

    def test_weekly_skips_non_matching_weekday(self):
        Expense.objects.create(
            description="Limpieza lunes",
            category="other",
            amount=Decimal("30.00"),
            expense_date=date(2026, 1, 1),
            is_recurring=True,
            recurring_frequency="weekly",
            recurring_weekdays="0",
        )
        # date(2026, 3, 3) is a Tuesday (weekday 1) — should not materialise.
        tuesday = date(2026, 3, 3)
        assert tuesday.weekday() == 1
        assert materialize_recurring_for_date(tuesday) == 0

    def test_weekly_multiple_weekdays(self):
        tpl = Expense.objects.create(
            description="Lun+Mar",
            category="other",
            amount=Decimal("15.00"),
            expense_date=date(2026, 1, 1),
            is_recurring=True,
            recurring_frequency="weekly",
            recurring_weekdays="0,1",
        )
        monday = date(2026, 3, 2)
        tuesday = date(2026, 3, 3)
        assert materialize_recurring_for_date(monday) == 1
        assert materialize_recurring_for_date(tuesday) == 1
        assert Expense.objects.filter(generated_from=tpl, is_recurring=False).count() == 2

    def test_weekly_idempotent_per_date(self):
        tpl = Expense.objects.create(
            description="Limpieza lunes",
            category="other",
            amount=Decimal("30.00"),
            expense_date=date(2026, 1, 1),
            is_recurring=True,
            recurring_frequency="weekly",
            recurring_weekdays="0",
        )
        monday = date(2026, 3, 2)
        materialize_recurring_for_date(monday)
        assert materialize_recurring_for_date(monday) == 0
        assert Expense.objects.filter(generated_from=tpl, expense_date=monday).count() == 1

    def test_yearly_materialises_on_matching_month_and_day(self):
        tpl = Expense.objects.create(
            description="Seguro anual",
            category="insurance",
            amount=Decimal("800.00"),
            expense_date=date(2026, 1, 1),
            is_recurring=True,
            recurring_frequency="yearly",
            recurring_day=10,
            recurring_month=6,
        )
        target = date(2026, 6, 10)
        created = materialize_recurring_for_date(target)
        assert created == 1
        assert Expense.objects.filter(generated_from=tpl, expense_date=target, is_recurring=False).count() == 1

    def test_yearly_skips_non_matching_date(self):
        Expense.objects.create(
            description="Seguro anual",
            category="insurance",
            amount=Decimal("800.00"),
            expense_date=date(2026, 1, 1),
            is_recurring=True,
            recurring_frequency="yearly",
            recurring_day=10,
            recurring_month=6,
        )
        assert materialize_recurring_for_date(date(2026, 6, 11)) == 0
        assert materialize_recurring_for_date(date(2026, 7, 10)) == 0

    def test_yearly_idempotent(self):
        tpl = Expense.objects.create(
            description="Seguro anual",
            category="insurance",
            amount=Decimal("800.00"),
            expense_date=date(2026, 1, 1),
            is_recurring=True,
            recurring_frequency="yearly",
            recurring_day=10,
            recurring_month=6,
        )
        target = date(2026, 6, 10)
        materialize_recurring_for_date(target)
        assert materialize_recurring_for_date(target) == 0
        assert Expense.objects.filter(generated_from=tpl).count() == 1

    def test_ignores_monthly_templates(self):
        Expense.objects.create(
            description="Alquiler",
            category="rent",
            amount=Decimal("500.00"),
            expense_date=date(2026, 1, 1),
            is_recurring=True,
            recurring_frequency="monthly",
            recurring_day=2,
        )
        # A Monday that also happens to be day 2 — must NOT materialise monthly here.
        assert materialize_recurring_for_date(date(2026, 3, 2)) == 0


class TestAmountParsing:
    """`Decimal()` accepts "NaN" and "Infinity" — they are values, not parse
    errors — so they sailed past `except InvalidOperation` and then raised on
    the very next line, because `Decimal("NaN") <= 0` signals InvalidOperation
    itself. Nothing caught that, so `amount=NaN` was an unhandled 500 on a form
    non-admin teachers can reach.
    """

    @pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_amounts_are_rejected(self, raw):
        assert _parse_amount(raw) is None

    @pytest.mark.parametrize("raw", ["", "abc", "1.2.3"])
    def test_unparseable_amounts_are_rejected(self, raw):
        """The other two branches of the same function."""
        assert _parse_amount(raw) is None

    @pytest.mark.parametrize(("raw", "expected"), [("12.50", Decimal("12.50")), ("12,50", Decimal("12.50"))])
    def test_ordinary_amounts_still_parse(self, raw, expected):
        """The guard must not over-reject: the comma decimal separator is how
        the form is actually filled in."""
        assert _parse_amount(raw) == expected

    def test_a_nan_amount_is_a_form_error_not_a_crash(self, authenticated_client):
        response = authenticated_client.post(
            reverse("create_expense"),
            {"description": "Material", "amount": "NaN", "category": "other"},
        )

        assert response.status_code == 302
        assert not Expense.objects.filter(description="Material").exists()


class TestExpenseListQueryStringIsRangeChecked:
    """Parsing was not enough. `expense_date__year` makes Django build a real
    `date(year, 1, 1)` for the bounds, so `?year=-5` raised ValueError and
    `?year=99999999999` raised OverflowError — both past an
    `except (TypeError, ValueError)` that only wrapped the `int()` call.
    """

    @pytest.mark.parametrize("year", ["-5", "99999999999", "abc"])
    def test_a_hostile_year_does_not_crash_the_page(self, authenticated_client, year):
        assert authenticated_client.get(reverse("expenses_list"), {"year": year}).status_code == 200

    @pytest.mark.parametrize("month", ["0", "13", "abc"])
    def test_a_hostile_month_does_not_crash_the_page(self, authenticated_client, month):
        assert authenticated_client.get(reverse("expenses_list"), {"month": month}).status_code == 200

    def test_a_real_filter_is_still_honoured(self, authenticated_client):
        response = authenticated_client.get(reverse("expenses_list"), {"year": "2024", "month": "3"})

        assert response.context["year"] == 2024
        assert response.context["month"] == 3
        assert response.context["default_expense_date"] == date(2024, 3, 1)
