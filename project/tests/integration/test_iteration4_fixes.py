"""Regression tests for the iteration-4 review fixes (email hardening, exports, seeds)."""

import pytest

pytestmark = pytest.mark.django_db


class TestEmailConnectionReuse:
    """#58 — EmailService supports a shared SMTP connection for batch sends."""

    def test_open_connection_returns_a_connection(self):
        from comms.services.email_service import email_service

        conn = email_service.open_connection()
        assert conn is not None

    def test_send_email_accepts_a_shared_connection(self, settings):
        from django.core import mail

        from comms.services.email_service import email_service

        conn = email_service.open_connection()
        ok = email_service.send_email(
            template_name="happy_birthday",
            recipients="parent@example.test",
            subject="Test",
            context={"name": "Ana"},
            connection=conn,
        )
        assert ok is True
        assert len(mail.outbox) == 1


class TestEmailTimeoutConfigured:
    """#58 — a hung SMTP socket cannot park a worker forever."""

    def test_email_timeout_is_set(self):
        from django.conf import settings as dj_settings

        # settings_test inherits EMAIL_TIMEOUT from settings.py (default 20).
        assert getattr(dj_settings, "EMAIL_TIMEOUT", None), "EMAIL_TIMEOUT must be configured"


class TestExcelExportBounded:
    """#63 — _auto_width does not inflate an empty sheet, and export runs."""

    def test_empty_workbook_has_only_headers(self):
        from billing.exports import build_database_workbook

        wb = build_database_workbook()
        for ws in wb.worksheets:
            assert ws.max_row == 1  # header only, not inflated to the sample size

    def test_export_with_rows(self, student, active_enrollment, pending_payment):
        from billing.exports import build_database_workbook

        wb = build_database_workbook()
        # Students / Matrículas / Pagos each have their header + at least one row.
        assert wb["Estudiantes"].max_row >= 2
        assert wb["Matrículas"].max_row >= 2
        assert wb["Pagos"].max_row >= 2
