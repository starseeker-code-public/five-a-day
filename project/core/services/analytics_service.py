"""
Analytics + reporting service (v1.7).

Aggregates monthly / yearly financial figures, retention, collection rate,
and group utilisation. Pure query helpers — no HTTP, no rendering, no PDF.
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce, ExtractMonth

from billing.constants import LIVE_PAYMENT_STATUSES
from billing.models import Expense, Payment
from students.models import Group, Student

_ZERO = Decimal("0.00")
_MONEY = DecimalField(max_digits=12, decimal_places=2)


def _sum(qs, field: str = "amount") -> Decimal:
    result = qs.aggregate(t=Coalesce(Sum(field), Value(_ZERO), output_field=_MONEY))
    return result["t"]


def financial_summary_month(month: int, year: int) -> dict[str, Any]:
    """
    Income, expenses, net for a single month plus per-category breakdown.
    Mirrors `billing.services.expense_service.monthly_totals` but adds the
    "pending" bucket so reports can surface uncollected receivables.
    """
    income = _sum(
        Payment.objects.filter(payment_status="completed", payment_date__month=month, payment_date__year=year)
    )
    pending = _sum(Payment.objects.filter(payment_status="pending", due_date__month=month, due_date__year=year))
    # The per-category breakdown already sums to the month total, so the separate
    # `Sum` that used to run alongside it was a second scan of the same rows for
    # a figure we were about to compute anyway.
    by_category = dict(
        Expense.objects.filter(is_recurring=False, expense_date__month=month, expense_date__year=year)
        .values("category")
        .annotate(total=Coalesce(Sum("amount"), Value(_ZERO), output_field=_MONEY))
        .values_list("category", "total")
    )
    expenses = sum(by_category.values(), _ZERO)
    return {
        "month": month,
        "year": year,
        "income": income,
        "pending": pending,
        "expenses": expenses,
        "net": income - expenses,
        "by_category": by_category,
    }


def _months_by_month(year: int) -> list[dict[str, Any]]:
    """The 12 monthly rows of `year`, resolved in THREE queries rather than 48.

    `financial_summary_year` used to call `financial_summary_month` twelve times,
    and each of those runs four separate aggregates — so the /reports/ page fired
    48 queries for the chart alone (57 for the whole page), independently of how
    much data the academy actually has. The work is the same aggregation twelve
    times over disjoint slices of one table, which is what GROUP BY is for.

    Three queries and not one because the buckets key on different columns:
    collected income is attributed to `payment_date`, receivables to `due_date`,
    and expenses live in another table. Grouping expenses by (month, category)
    also yields the month total, so the separate `Sum` that used to run beside
    the per-category breakdown is gone.
    """
    income: dict[int, Decimal] = {
        row["m"]: row["total"]
        for row in Payment.objects.filter(payment_status="completed", payment_date__year=year)
        .annotate(m=ExtractMonth("payment_date"))
        .values("m")
        .annotate(total=Coalesce(Sum("amount"), Value(_ZERO), output_field=_MONEY))
        .values("m", "total")
    }
    pending: dict[int, Decimal] = {
        row["m"]: row["total"]
        for row in Payment.objects.filter(payment_status="pending", due_date__year=year)
        .annotate(m=ExtractMonth("due_date"))
        .values("m")
        .annotate(total=Coalesce(Sum("amount"), Value(_ZERO), output_field=_MONEY))
        .values("m", "total")
    }
    by_category: dict[int, dict[str, Decimal]] = {m: {} for m in range(1, 13)}
    expenses: dict[int, Decimal] = {}
    for row in (
        Expense.objects.filter(is_recurring=False, expense_date__year=year)
        .annotate(m=ExtractMonth("expense_date"))
        .values("m", "category")
        .annotate(total=Coalesce(Sum("amount"), Value(_ZERO), output_field=_MONEY))
        .values("m", "category", "total")
    ):
        month = row["m"]
        by_category.setdefault(month, {})[row["category"]] = row["total"]
        expenses[month] = expenses.get(month, _ZERO) + row["total"]

    rows = []
    for m in range(1, 13):
        month_income = income.get(m, _ZERO)
        month_expenses = expenses.get(m, _ZERO)
        rows.append(
            {
                "month": m,
                "year": year,
                "income": month_income,
                "pending": pending.get(m, _ZERO),
                "expenses": month_expenses,
                "net": month_income - month_expenses,
                "by_category": by_category.get(m, {}),
            }
        )
    return rows


def financial_summary_year(year: int) -> dict[str, Any]:
    """Yearly aggregate plus 12 monthly rows for chart-friendly rendering."""
    months = _months_by_month(year)
    return {
        "year": year,
        "income": sum((m["income"] for m in months), _ZERO),
        "expenses": sum((m["expenses"] for m in months), _ZERO),
        "net": sum((m["net"] for m in months), _ZERO),
        "months": months,
    }


def collection_rate(month: int, year: int) -> dict[str, Any]:
    """Ratio of collected to expected payments in a month (0.0-1.0).

    "Expected" excludes cancelled / failed / refunded payments: money that will
    never arrive shouldn't sit in the denominator. Cancelling one duplicate
    payment used to drag the whole month's collection rate toward 0%.
    """
    zero = Decimal("0.00")
    expected = _sum(
        Payment.objects.filter(
            due_date__month=month,
            due_date__year=year,
            payment_status__in=LIVE_PAYMENT_STATUSES,
        )
    )
    collected = _sum(Payment.objects.filter(payment_status="completed", due_date__month=month, due_date__year=year))
    ratio = (collected / expected) if expected else zero
    return {
        "expected": expected,
        "collected": collected,
        "rate": ratio.quantize(Decimal("0.0001")) if expected else zero,
        "percent": (ratio * 100).quantize(Decimal("0.01")) if expected else zero,
    }


def retention_snapshot(reference: date | None = None) -> dict[str, Any]:
    """
    Simple retention: how many students that were active a year ago are still
    active today? Used for the yearly report card, not a rigorous cohort
    analysis.
    """
    from datetime import datetime, time

    today = reference or date.today()
    one_year_ago = today - timedelta(days=365)
    # `created_at` is a tz-aware DateTimeField — compare against a tz-aware value
    # to avoid Django's "naive datetime while time zone support is active" warning.
    one_year_ago_dt = datetime.combine(one_year_ago, time.min, tzinfo=UTC)

    baseline_students = Student.objects.filter(created_at__lte=one_year_ago_dt)
    baseline_count = baseline_students.count()
    still_active_count = baseline_students.filter(active=True).count()

    ratio = Decimal("0.00")
    if baseline_count:
        ratio = (Decimal(still_active_count) / Decimal(baseline_count)).quantize(Decimal("0.0001"))

    return {
        "baseline": baseline_count,
        "still_active": still_active_count,
        "retention_rate": ratio,
        "retention_percent": (ratio * 100).quantize(Decimal("0.01")),
    }


def group_utilisation() -> list[dict[str, Any]]:
    """
    Per-group utilisation = enrolled / max_students (percentage), with waiter
    count. Groups without a cap show `utilisation=None`.
    """
    groups = (
        Group.objects.filter(active=True)
        .annotate(
            enrolled=Count("students", filter=Q(students__active=True, students__is_waiting=False), distinct=True),
            waiting=Count("students", filter=Q(students__active=True, students__is_waiting=True), distinct=True),
        )
        .select_related("teacher")
        .order_by("group_name")
    )

    rows: list[dict[str, Any]] = []
    for g in groups:
        util = None
        if g.max_students:
            util = (Decimal(g.enrolled) / Decimal(g.max_students) * 100).quantize(Decimal("0.01"))
        rows.append(
            {
                "id": g.id,
                "name": g.group_name,
                "teacher": g.teacher.full_name,
                "enrolled": g.enrolled,
                "waiting": g.waiting,
                "max_students": g.max_students,
                "utilisation_percent": util,
            }
        )
    return rows


def dashboard_report(month: int | None = None, year: int | None = None) -> dict[str, Any]:
    """Bundle everything the /reports/ page needs into one call."""
    today = date.today()
    m = month or today.month
    y = year or today.year
    # One pass over the year, then pick the requested month out of it. Calling
    # `financial_summary_month(m, y)` here as well re-ran four aggregates for a
    # row `financial_summary_year` had already computed.
    year_totals = financial_summary_year(y)
    return {
        "current_month": year_totals["months"][m - 1],
        "year_totals": year_totals,
        "collection": collection_rate(m, y),
        "retention": retention_snapshot(today),
        "groups": group_utilisation(),
        "months_labels": [calendar.month_abbr[i] for i in range(1, 13)],
    }


__all__ = [
    "collection_rate",
    "dashboard_report",
    "financial_summary_month",
    "financial_summary_year",
    "group_utilisation",
    "retention_snapshot",
]
