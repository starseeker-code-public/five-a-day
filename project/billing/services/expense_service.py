"""
Expense reporting + recurring-template materialisation (v1.5).

Kept intentionally thin — models handle validation, views handle HTTP; this
module owns only the multi-record aggregations and the "materialise recurring
templates for this month" flow so both the dashboard widget and the Celery
Beat job (v1.4) share the same code path.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from billing.models import Expense, Payment

logger = logging.getLogger(__name__)


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


def _create_if_absent(tpl: Expense, target_date: date) -> int:
    """
    Materialise a concrete Expense row from `tpl` on `target_date` unless one
    already exists. Idempotent — matches on `generated_from` + exact date so
    re-running any materialisation task never double-creates. Returns 1 if a
    row was created, 0 otherwise.
    """
    already_exists = Expense.objects.filter(
        generated_from=tpl,
        expense_date=target_date,
    ).exists()
    if already_exists:
        return 0

    # The check above is a read-then-write with nothing behind it, and the monthly
    # and daily materialisers run on different cadences over overlapping templates,
    # so both could pass it and each create a row.
    # `expenses.unique_materialized_expense_per_date` is the real guarantee; losing
    # the race means the row now exists, which is the outcome we wanted.
    try:
        with transaction.atomic():
            Expense.objects.create(
                description=tpl.description,
                category=tpl.category,
                amount=tpl.amount,
                expense_date=target_date,
                notes=tpl.notes,
                is_recurring=False,
                generated_from=tpl,
            )
    except IntegrityError:
        logger.warning(
            "Recurring expense from template %d for %s already existed; skipped.",
            int(tpl.pk),
            target_date.isoformat(),
        )
        return 0
    return 1


def materialize_recurring(month: int, year: int) -> int:
    """
    Materialise every MONTHLY recurring template into a concrete Expense row for
    the given month, if one hasn't already been generated. Returns the number of
    rows created. Runs from the monthly Celery Beat job.

    Idempotent — see `_create_if_absent`.
    """
    templates = Expense.objects.filter(is_recurring=True, recurring_frequency="monthly")
    created = 0
    last_day_of_month = calendar.monthrange(year, month)[1]

    for tpl in templates:
        # Day-of-month is capped at 28 by validation; clamp anyway to be safe.
        day = min(tpl.recurring_day or 1, last_day_of_month)
        target_date = date(year, month, day)
        created += _create_if_absent(tpl, target_date)
    return created


def materialize_recurring_for_date(target_date: date) -> int:
    """
    Materialise WEEKLY and YEARLY recurring templates that fall on `target_date`.
    Runs daily from Celery Beat.

    - weekly: template materialises when `target_date.weekday()` is in its
      selected weekday set (once per matching day).
    - yearly: template materialises when `target_date` month + day match.

    Idempotent — see `_create_if_absent`.
    """
    created = 0
    weekday = target_date.weekday()

    weekly = Expense.objects.filter(is_recurring=True, recurring_frequency="weekly")
    for tpl in weekly:
        if weekday in tpl.weekday_set():
            created += _create_if_absent(tpl, target_date)

    yearly = Expense.objects.filter(
        is_recurring=True,
        recurring_frequency="yearly",
        recurring_month=target_date.month,
        recurring_day=target_date.day,
    )
    for tpl in yearly:
        created += _create_if_absent(tpl, target_date)

    return created


__all__ = ["monthly_totals", "materialize_recurring", "materialize_recurring_for_date"]
