"""
Analytics + reporting service (v1.7).

Aggregates monthly / yearly financial figures, retention, collection rate,
and group utilisation. Pure query helpers — no HTTP, no rendering, no PDF.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce

from billing.models import Expense, Payment
from students.models import Group, Student

_ZERO = Decimal("0.00")


def _sum(qs, field: str = "amount") -> Decimal:
    result = qs.aggregate(
        t=Coalesce(Sum(field), Value(_ZERO), output_field=DecimalField(max_digits=12, decimal_places=2))
    )
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
    expenses = _sum(Expense.objects.filter(is_recurring=False, expense_date__month=month, expense_date__year=year))
    by_category = dict(
        Expense.objects.filter(is_recurring=False, expense_date__month=month, expense_date__year=year)
        .values("category")
        .annotate(
            total=Coalesce(Sum("amount"), Value(_ZERO), output_field=DecimalField(max_digits=12, decimal_places=2))
        )
        .values_list("category", "total")
    )
    return {
        "month": month,
        "year": year,
        "income": income,
        "pending": pending,
        "expenses": expenses,
        "net": income - expenses,
        "by_category": by_category,
    }


def financial_summary_year(year: int) -> dict[str, Any]:
    """Yearly aggregate plus 12 monthly rows for chart-friendly rendering."""
    months = [financial_summary_month(m, year) for m in range(1, 13)]
    return {
        "year": year,
        "income": sum((m["income"] for m in months), _ZERO),
        "expenses": sum((m["expenses"] for m in months), _ZERO),
        "net": sum((m["net"] for m in months), _ZERO),
        "months": months,
    }


def collection_rate(month: int, year: int) -> dict[str, Any]:
    """Ratio of collected to expected payments in a month (0.0-1.0)."""
    zero = Decimal("0.00")
    expected = _sum(Payment.objects.filter(due_date__month=month, due_date__year=year))
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
    today = reference or date.today()
    one_year_ago = today - timedelta(days=365)

    baseline_students = Student.objects.filter(created_at__lte=one_year_ago)
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
    return {
        "current_month": financial_summary_month(m, y),
        "year_totals": financial_summary_year(y),
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
