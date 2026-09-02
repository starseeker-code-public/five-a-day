"""Integration tests for the Expense CRUD endpoints (v1.5)."""

from datetime import date
from decimal import Decimal

import pytest
from django.test import override_settings
from django.urls import reverse

from billing.models import Expense

pytestmark = pytest.mark.django_db


class TestExpensesListView:
    def test_loads_ok(self, authenticated_client):
        response = authenticated_client.get(reverse("expenses_list"))
        assert response.status_code == 200

    def test_filters_by_category(self, authenticated_client):
        Expense.objects.create(description="Rent", category="rent", amount=Decimal("500"), expense_date=date.today())
        Expense.objects.create(
            description="Supplies", category="supplies", amount=Decimal("30"), expense_date=date.today()
        )
        response = authenticated_client.get(
            reverse("expenses_list"),
            {"category": "rent", "month": date.today().month, "year": date.today().year},
        )
        assert response.status_code == 200
        descs = [e.description for e in response.context["expenses"]]
        assert "Rent" in descs
        assert "Supplies" not in descs

    def test_totals_present(self, authenticated_client):
        response = authenticated_client.get(reverse("expenses_list"))
        assert "totals" in response.context
        assert "income" in response.context["totals"]
        assert "expenses" in response.context["totals"]


class TestExpensesGcpLiveRow:
    """The RUNNING month's GCP spend is dynamic (live from the billing export);
    finished months only ever show the archived Expense row."""

    def _configured(self, amount):
        from unittest.mock import patch

        from billing.services import gcp_cost_service

        return (
            patch.object(gcp_cost_service, "is_configured", return_value=True),
            patch.object(gcp_cost_service, "month_cost", return_value=amount),
        )

    def test_current_month_shows_live_row(self, authenticated_client):
        configured, cost = self._configured(Decimal("2.23"))
        with configured, cost:
            response = authenticated_client.get(reverse("expenses_list"))
        assert response.context["gcp_live"] == Decimal("2.23")
        assert b"mes en curso" in response.content

    def test_totals_include_live_amount(self, authenticated_client):
        configured, cost = self._configured(Decimal("2.23"))
        with configured, cost:
            with_live = authenticated_client.get(reverse("expenses_list"))
        without_live = authenticated_client.get(reverse("expenses_list"))
        assert with_live.context["totals"]["expenses"] - without_live.context["totals"]["expenses"] == Decimal("2.23")
        assert without_live.context["totals"]["net"] - with_live.context["totals"]["net"] == Decimal("2.23")

    def test_past_month_never_queries_live(self, authenticated_client):
        from unittest.mock import patch

        from billing.services import gcp_cost_service

        past = date.today().replace(day=1)
        past_month = 12 if past.month == 1 else past.month - 1
        past_year = past.year - 1 if past.month == 1 else past.year
        with (
            patch.object(gcp_cost_service, "is_configured", return_value=True),
            patch.object(gcp_cost_service, "month_cost") as cost,
        ):
            response = authenticated_client.get(reverse("expenses_list"), {"month": past_month, "year": past_year})
        assert response.context["gcp_live"] is None
        cost.assert_not_called()

    def test_archived_row_suppresses_live_figure(self, authenticated_client):
        """Once the month is archived, the saved row is the source of truth —
        the live figure must not be added on top of it."""
        from billing.services.gcp_cost_service import GCP_EXPENSE_DESCRIPTION

        Expense.objects.create(
            description=GCP_EXPENSE_DESCRIPTION,
            category="software",
            amount=Decimal("9.99"),
            expense_date=date.today(),
        )
        configured, cost = self._configured(Decimal("2.23"))
        with configured, cost as cost_mock:
            response = authenticated_client.get(reverse("expenses_list"))
        assert response.context["gcp_live"] is None
        cost_mock.assert_not_called()

    def test_category_filter_hides_row_but_keeps_totals(self, authenticated_client):
        configured, cost = self._configured(Decimal("2.23"))
        with configured, cost:
            response = authenticated_client.get(
                reverse("expenses_list"),
                {"category": "rent", "month": date.today().month, "year": date.today().year},
            )
        assert response.context["gcp_live"] is None
        assert b"mes en curso" not in response.content
        # Month-wide summary cards still include it, like monthly_totals itself.
        assert response.context["totals"]["expenses"] >= Decimal("2.23")

    # The local .env may set GCP_BILLING_EXPORT_TABLE, so unconfigured is forced.
    @override_settings(GCP_BILLING_EXPORT_TABLE="")
    def test_unconfigured_is_silent(self, authenticated_client):
        response = authenticated_client.get(reverse("expenses_list"))
        assert response.context["gcp_live"] is None
        assert b"mes en curso" not in response.content


class TestCreateExpenseView:
    def test_creates_row(self, authenticated_client):
        before = Expense.objects.count()
        response = authenticated_client.post(
            reverse("create_expense"),
            {
                "description": "Alquiler",
                "category": "rent",
                "amount": "500.00",
                "expense_date": "2026-03-05",
                "notes": "",
            },
        )
        assert response.status_code == 302
        assert Expense.objects.count() == before + 1

    def test_rejects_missing_description(self, authenticated_client):
        before = Expense.objects.count()
        response = authenticated_client.post(
            reverse("create_expense"),
            {"category": "rent", "amount": "500.00"},
        )
        assert response.status_code == 302  # redirect back with error message
        assert Expense.objects.count() == before

    def test_recurring_expense(self, authenticated_client):
        response = authenticated_client.post(
            reverse("create_expense"),
            {
                "description": "Alquiler",
                "category": "rent",
                "amount": "500.00",
                "expense_date": "2026-03-05",
                "is_recurring": "on",
                "recurring_day": "5",
            },
        )
        assert response.status_code == 302
        recurring = Expense.objects.filter(is_recurring=True).last()
        assert recurring.recurring_day == 5
        assert recurring.recurring_frequency == "monthly"

    def test_yearly_recurring_expense(self, authenticated_client):
        response = authenticated_client.post(
            reverse("create_expense"),
            {
                "description": "Seguro anual",
                "category": "insurance",
                "amount": "800.00",
                "expense_date": "2026-03-05",
                "is_recurring": "on",
                "recurring_frequency": "yearly",
                "recurring_day": "10",
                "recurring_month": "6",
            },
        )
        assert response.status_code == 302
        recurring = Expense.objects.filter(is_recurring=True, recurring_frequency="yearly").last()
        assert recurring is not None
        assert recurring.recurring_day == 10
        assert recurring.recurring_month == 6

    def test_weekly_recurring_expense(self, authenticated_client):
        response = authenticated_client.post(
            reverse("create_expense"),
            {
                "description": "Limpieza",
                "category": "other",
                "amount": "30.00",
                "expense_date": "2026-03-05",
                "is_recurring": "on",
                "recurring_frequency": "weekly",
                "recurring_weekdays": ["0", "2", "4"],
            },
        )
        assert response.status_code == 302
        recurring = Expense.objects.filter(is_recurring=True, recurring_frequency="weekly").last()
        assert recurring is not None
        assert recurring.weekday_set() == {0, 2, 4}

    def test_weekly_without_weekdays_rejected(self, authenticated_client):
        before = Expense.objects.count()
        response = authenticated_client.post(
            reverse("create_expense"),
            {
                "description": "Limpieza",
                "category": "other",
                "amount": "30.00",
                "is_recurring": "on",
                "recurring_frequency": "weekly",
            },
        )
        assert response.status_code == 302  # redirect back with error message
        assert Expense.objects.count() == before


class TestDeleteExpenseView:
    def test_deletes(self, authenticated_client):
        e = Expense.objects.create(description="X", category="other", amount=Decimal("1"), expense_date=date.today())
        response = authenticated_client.post(reverse("delete_expense", args=[e.id]))
        assert response.status_code == 302
        assert not Expense.objects.filter(id=e.id).exists()

    def test_delete_404(self, authenticated_client):
        response = authenticated_client.post(reverse("delete_expense", args=[999999]))
        assert response.status_code == 404
