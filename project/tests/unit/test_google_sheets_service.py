"""Unit tests for the Google Sheets export service (v1.2)."""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from core.services.google_sheets_service import (
    ExportResult,
    GoogleSheetsService,
    _load_service_account_info,
    get_service,
)

pytestmark = pytest.mark.django_db


class TestConfiguration:
    def test_not_configured_when_missing_credentials(self):
        with override_settings(
            GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON="",
            GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE="",
            GOOGLE_SHEETS_SPREADSHEET_ID="abc",
        ):
            assert GoogleSheetsService().is_configured() is False

    def test_not_configured_when_missing_spreadsheet_id(self):
        with override_settings(
            GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON='{"type":"service_account"}',
            GOOGLE_SHEETS_SPREADSHEET_ID="",
        ):
            assert GoogleSheetsService().is_configured() is False

    def test_configured_with_inline_json(self):
        with override_settings(
            GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON='{"type":"service_account"}',
            GOOGLE_SHEETS_SPREADSHEET_ID="abc",
        ):
            assert GoogleSheetsService().is_configured() is True

    def test_load_service_account_info_inline(self):
        payload = {"type": "service_account", "client_email": "a@b.iam"}
        with override_settings(
            GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON=json.dumps(payload),
        ):
            assert _load_service_account_info() == payload

    def test_load_service_account_info_malformed_json(self):
        with override_settings(
            GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON="not json",
            GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE="",
        ):
            assert _load_service_account_info() is None

    def test_load_service_account_info_missing_file(self, tmp_path):
        with override_settings(
            GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON="",
            GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE=str(tmp_path / "nonexistent.json"),
        ):
            assert _load_service_account_info() is None

    def test_load_service_account_info_from_file(self, tmp_path):
        payload = {"type": "service_account"}
        path = tmp_path / "creds.json"
        path.write_text(json.dumps(payload))
        with override_settings(
            GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON="",
            GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE=str(path),
        ):
            assert _load_service_account_info() == payload


class TestExportStudents:
    def test_exports_active_students(self, student, active_enrollment):
        svc = GoogleSheetsService(spreadsheet_id="fake")
        ws = MagicMock()
        with patch.object(svc, "_get_or_create_worksheet", return_value=ws):
            result = svc.export_students(worksheet_name="Students")
        assert result.success is True
        assert result.rows_written == 1  # only the one student
        ws.clear.assert_called_once()
        ws.update.assert_called_once()
        rows = ws.update.call_args[0][0]
        assert rows[0][0] == "ID"  # header row
        assert rows[1][1] == student.first_name

    def test_excludes_inactive_students(self, inactive_student, student, active_enrollment):
        svc = GoogleSheetsService(spreadsheet_id="fake")
        ws = MagicMock()
        with patch.object(svc, "_get_or_create_worksheet", return_value=ws):
            result = svc.export_students()
        assert result.rows_written == 1

    def test_returns_error_on_exception(self):
        svc = GoogleSheetsService(spreadsheet_id="fake")
        with patch.object(svc, "_get_or_create_worksheet", side_effect=RuntimeError("boom")):
            result = svc.export_students()
        assert result.success is False
        assert "boom" in result.error


class TestExportPayments:
    def test_exports_payments_for_current_year(self, pending_payment):
        svc = GoogleSheetsService(spreadsheet_id="fake")
        ws = MagicMock()
        with patch.object(svc, "_get_or_create_worksheet", return_value=ws):
            result = svc.export_payments(academic_year=pending_payment.enrollment.academic_year)
        assert result.success is True
        assert result.rows_written == 1

    def test_returns_error_on_exception(self):
        svc = GoogleSheetsService(spreadsheet_id="fake")
        with patch.object(svc, "_get_or_create_worksheet", side_effect=RuntimeError("boom")):
            result = svc.export_payments()
        assert result.success is False
        assert "boom" in result.error


class TestExportResult:
    def test_as_dict(self):
        r = ExportResult(success=True, worksheet="X", rows_written=5, error="")
        d = r.as_dict()
        assert d == {"success": True, "worksheet": "X", "rows_written": 5, "error": ""}


class TestGetService:
    def test_returns_service_instance(self):
        svc = get_service()
        assert isinstance(svc, GoogleSheetsService)
