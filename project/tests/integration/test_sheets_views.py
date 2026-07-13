"""Integration tests for the Google Sheets export view (v1.2)."""

from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import reverse

from core.services.google_sheets_service import ExportResult

pytestmark = pytest.mark.django_db


class TestExportToSheetsView:
    def test_requires_post(self, authenticated_client):
        response = authenticated_client.get(reverse("export_to_sheets"))
        assert response.status_code == 405

    def test_returns_503_when_unconfigured(self, authenticated_client):
        with override_settings(
            GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON="",
            GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE="",
            GOOGLE_SHEETS_SPREADSHEET_ID="",
        ):
            response = authenticated_client.post(reverse("export_to_sheets"))
        assert response.status_code == 503
        body = response.json()
        assert body["success"] is False
        assert "no configurada" in body["error"].lower()

    def test_rejects_invalid_target(self, authenticated_client):
        with override_settings(
            GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON='{"type":"service_account"}',
            GOOGLE_SHEETS_SPREADSHEET_ID="abc",
        ):
            response = authenticated_client.post(reverse("export_to_sheets"), {"target": "invalid"})
        assert response.status_code == 400

    def test_successful_export_students_only(self, authenticated_client):
        with override_settings(
            GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON='{"type":"service_account"}',
            GOOGLE_SHEETS_SPREADSHEET_ID="abc",
        ):
            with patch(
                "core.services.google_sheets_service.GoogleSheetsService.export_students",
                return_value=ExportResult(success=True, worksheet="Students", rows_written=3),
            ):
                response = authenticated_client.post(reverse("export_to_sheets"), {"target": "students"})
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["target"] == "students"
        assert len(body["results"]) == 1
        assert body["results"][0]["rows_written"] == 3

    def test_successful_export_both(self, authenticated_client):
        with override_settings(
            GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON='{"type":"service_account"}',
            GOOGLE_SHEETS_SPREADSHEET_ID="abc",
        ):
            with (
                patch(
                    "core.services.google_sheets_service.GoogleSheetsService.export_students",
                    return_value=ExportResult(success=True, worksheet="Students", rows_written=1),
                ),
                patch(
                    "core.services.google_sheets_service.GoogleSheetsService.export_payments",
                    return_value=ExportResult(success=True, worksheet="Payments", rows_written=2),
                ),
            ):
                response = authenticated_client.post(reverse("export_to_sheets"))
        assert response.status_code == 200
        body = response.json()
        assert body["target"] == "both"
        assert len(body["results"]) == 2

    def test_partial_failure_returns_502(self, authenticated_client):
        with override_settings(
            GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON='{"type":"service_account"}',
            GOOGLE_SHEETS_SPREADSHEET_ID="abc",
        ):
            with patch(
                "core.services.google_sheets_service.GoogleSheetsService.export_students",
                return_value=ExportResult(success=False, worksheet="Students", error="boom"),
            ):
                response = authenticated_client.post(reverse("export_to_sheets"), {"target": "students"})
        assert response.status_code == 502
        body = response.json()
        assert body["success"] is False
