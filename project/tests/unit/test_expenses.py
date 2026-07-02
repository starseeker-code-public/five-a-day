"""Unit tests for the Expense model + service (v1.5)."""

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from billing.models import Expense
from billing.services.expense_service import materialize_recurring, monthly_totals

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

    def test_recurring_day_out_of_range(self):
        e = Expense(
            description="X",
            category="other",
            amount=Decimal("1.00"),
            expense_date=date(2026, 3, 1),
            is_recurring=True,
            recurring_day=30,
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
