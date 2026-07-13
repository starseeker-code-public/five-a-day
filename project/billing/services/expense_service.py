"""
Expense reporting + recurring-template materialisation (v1.5).

Kept intentionally thin — models handle validation, views handle HTTP; this
module owns only the multi-record aggregations and the "materialise recurring
templates for this month" flow so both the dashboard widget and the Celery
Beat job (v1.4) share the same code path.
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from billing.models import Expense, Payment


def monthly_totals(month: int, year: int) -> dict[str, Decimal | dict[str, Decimal]]:
    """
    Return the month's income (completed payments), total expenses, net
    profit, and a per-category expense breakdown. Used by the dashboard
    widget and by the monthly-report Celery task.
    """
    zero = Decimal("0.00")

    income = (
        Payment.objects.filter(
            payment_status="completed",
            payment_date__month=month,
            payment_date__year=year,
        ).aggregate(
            total=Coalesce(Sum("amount"), Value(zero), output_field=DecimalField(max_digits=12, decimal_places=2))
        )
    )["total"]

    expenses_qs = Expense.objects.filter(
        expense_date__month=month,
        expense_date__year=year,
        is_recurring=False,
    )
    expense_total = expenses_qs.aggregate(
        total=Coalesce(Sum("amount"), Value(zero), output_field=DecimalField(max_digits=12, decimal_places=2))
    )["total"]

    by_category = dict(
        expenses_qs.values("category")
        .annotate(
            total=Coalesce(Sum("amount"), Value(zero), output_field=DecimalField(max_digits=12, decimal_places=2))
        )
        .values_list("category", "total")
    )

    return {
        "income": income,
        "expenses": expense_total,
        "net": income - expense_total,
        "by_category": by_category,
    }


def materialize_recurring(month: int, year: int) -> int:
    """
    For every recurring Expense template with `is_recurring=True`, create a
    concrete Expense row for the given month if one hasn't already been
    generated. Returns the number of rows created.

    Idempotent — the `generated_from` FK is used to skip templates that
    already have a materialised row for the month.
    """
    templates = Expense.objects.filter(is_recurring=True)
    created = 0
    last_day_of_month = calendar.monthrange(year, month)[1]

    for tpl in templates:
        # Day-of-month is capped at 28 by validation; clamp anyway to be safe.
        day = min(tpl.recurring_day or 1, last_day_of_month)
        target_date = date(year, month, day)

        already_exists = Expense.objects.filter(
            generated_from=tpl,
            expense_date__month=month,
            expense_date__year=year,
        ).exists()
        if already_exists:
            continue

        Expense.objects.create(
            description=tpl.description,
            category=tpl.category,
            amount=tpl.amount,
            expense_date=target_date,
            notes=tpl.notes,
            is_recurring=False,
            generated_from=tpl,
        )
        created += 1
    return created


__all__ = ["monthly_totals", "materialize_recurring"]
