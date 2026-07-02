"""Integration tests for the Expense CRUD endpoints (v1.5)."""

from datetime import date
from decimal import Decimal

import pytest
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


class TestDeleteExpenseView:
    def test_deletes(self, authenticated_client):
        e = Expense.objects.create(description="X", category="other", amount=Decimal("1"), expense_date=date.today())
        response = authenticated_client.post(reverse("delete_expense", args=[e.id]))
        assert response.status_code == 302
        assert not Expense.objects.filter(id=e.id).exists()

    def test_delete_404(self, authenticated_client):
        response = authenticated_client.post(reverse("delete_expense", args=[999999]))
        assert response.status_code == 404
