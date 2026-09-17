"""One SMTP session per batch, a timeout on it, and a bounded Excel export.

The first two are what stop a mass mail paying a TLS+AUTH handshake per
recipient and what stops a stalled server wedging the request. The third stops
a growing roll turning one download into an unbounded one.

From the iteration-4 review."""

import pytest
from django.conf import settings as dj_settings
from django.core import mail

from billing.exports import build_database_workbook
from comms.services.email_service import email_service

pytestmark = pytest.mark.django_db


class TestEmailConnectionReuse:
    """#58 — EmailService supports a shared SMTP connection for batch sends."""

    def test_open_connection_returns_a_connection(self):
        conn = email_service.open_connection()
        assert conn is not None

    def test_send_email_accepts_a_shared_connection(self, settings):
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
        # settings_test inherits EMAIL_TIMEOUT from settings.py (default 20).
        assert getattr(dj_settings, "EMAIL_TIMEOUT", None), "EMAIL_TIMEOUT must be configured"


class TestExcelExportBounded:
    """#63 — _auto_width does not inflate an empty sheet, and export runs."""

    def test_empty_workbook_has_only_headers(self):
        wb = build_database_workbook()
        for ws in wb.worksheets:
            assert ws.max_row == 1  # header only, not inflated to the sample size

    def test_export_with_rows(self, student, active_enrollment, pending_payment):
        wb = build_database_workbook()
        # Students / Matrículas / Pagos each have their header + at least one row.
        assert wb["Estudiantes"].max_row >= 2
        assert wb["Matrículas"].max_row >= 2
        assert wb["Pagos"].max_row >= 2
