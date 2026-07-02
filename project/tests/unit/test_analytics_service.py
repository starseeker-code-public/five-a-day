"""Unit tests for the analytics service (v1.7)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from billing.models import Expense
from core.services.analytics_service import (
    collection_rate,
    dashboard_report,
    financial_summary_month,
    financial_summary_year,
    group_utilisation,
    retention_snapshot,
)

pytestmark = pytest.mark.django_db


class TestFinancialSummary:
    def test_month_empty(self):
        s = financial_summary_month(1, 2026)
        assert s["income"] == Decimal("0.00")
        assert s["expenses"] == Decimal("0.00")
        assert s["net"] == Decimal("0.00")

    def test_month_with_data(self, completed_payment):
        pd = completed_payment.payment_date
        Expense.objects.create(
            description="Rent", category="rent", amount=Decimal("200.00"), expense_date=pd.replace(day=1)
        )
        s = financial_summary_month(pd.month, pd.year)
        assert s["income"] == completed_payment.amount
        assert s["expenses"] == Decimal("200.00")

    def test_year_totals(self, completed_payment):
        pd = completed_payment.payment_date
        year = financial_summary_year(pd.year)
        assert year["income"] >= completed_payment.amount
        assert len(year["months"]) == 12


class TestCollectionRate:
    def test_empty(self):
        r = collection_rate(1, 2026)
        assert r["expected"] == Decimal("0.00")
        assert r["percent"] == Decimal("0.00")

    def test_full_collection(self, completed_payment):
        r = collection_rate(completed_payment.due_date.month, completed_payment.due_date.year)
        assert r["percent"] == Decimal("100.00")


class TestRetentionSnapshot:
    def test_no_baseline_returns_zero(self):
        r = retention_snapshot(date(2020, 1, 1))
        assert r["retention_percent"] == Decimal("0.00")

    def test_100_percent_retention(self, student):
        old_created = date.today() - timedelta(days=400)
        student.__class__.objects.filter(pk=student.pk).update(created_at=old_created)
        r = retention_snapshot()
        assert r["baseline"] >= 1
        assert r["still_active"] >= 1


class TestGroupUtilisation:
    def test_returns_rows_for_active_groups(self, group):
        rows = group_utilisation()
        ids = [r["id"] for r in rows]
        assert group.id in ids

    def test_no_cap_returns_none_utilisation(self, group):
        row = next(r for r in group_utilisation() if r["id"] == group.id)
        assert row["utilisation_percent"] is None

    def test_utilisation_with_cap(self, group, student):
        group.max_students = 2
        group.save()
        row = next(r for r in group_utilisation() if r["id"] == group.id)
        assert row["utilisation_percent"] == Decimal("50.00")


class TestDashboardReport:
    def test_bundles_everything(self):
        r = dashboard_report(1, 2026)
        assert "current_month" in r
        assert "year_totals" in r
        assert "collection" in r
        assert "retention" in r
        assert "groups" in r
        assert len(r["months_labels"]) == 12
