"""Integration tests for the reports dashboard (v1.7)."""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


class TestReportsView:
    def test_loads_ok(self, authenticated_client):
        response = authenticated_client.get(reverse("reports_view"))
        assert response.status_code == 200
        assert "report" in response.context

    def test_month_year_params(self, authenticated_client):
        response = authenticated_client.get(reverse("reports_view"), {"month": 3, "year": 2026})
        assert response.status_code == 200
        assert response.context["month"] == 3
        assert response.context["year"] == 2026

    def test_invalid_params_fallback_to_today(self, authenticated_client):
        response = authenticated_client.get(reverse("reports_view"), {"month": "invalid", "year": "invalid"})
        assert response.status_code == 200


class TestReportsPdf:
    def test_returns_pdf(self, authenticated_client):
        response = authenticated_client.get(reverse("reports_pdf"))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response.content.startswith(b"%PDF-")
