"""Unit tests for the v1.4 Celery Beat tasks."""

from unittest.mock import patch

import pytest
from django.test import override_settings

from billing.tasks import generate_monthly_payments_task
from comms.tasks import send_monthly_report_task

pytestmark = pytest.mark.django_db


class TestGenerateMonthlyPaymentsTask:
    def test_calls_management_command(self):
        with patch("django.core.management.call_command") as mock_call:
            result = generate_monthly_payments_task.run(month=3, year=2026)
        assert result["status"] == "success"
        assert result["month"] == 3
        assert result["year"] == 2026
        mock_call.assert_called_once()
        args, kwargs = mock_call.call_args
        assert args[0] == "generate_payments"
        assert kwargs["month"] == 3
        assert kwargs["year"] == 2026

    def test_defaults_to_today(self):
        with patch("django.core.management.call_command") as mock_call:
            generate_monthly_payments_task.run()
        assert mock_call.called


class TestSendMonthlyReportTask:
    def test_skips_when_no_recipient(self):
        with override_settings(SUPPORT_EMAIL=None):
            result = send_monthly_report_task.run()
        assert result["status"] == "skipped"

    def test_sends_when_recipient_configured(self, pending_payment, completed_payment):
        with override_settings(SUPPORT_EMAIL="admin@example.com"):
            with patch("comms.services.email_service.EmailService.send_email", return_value=True) as mock_send:
                result = send_monthly_report_task.run()
        assert result["status"] == "success"
        assert float(result["expected"]) >= 0
        assert float(result["collected"]) >= 0
        mock_send.assert_called_once()

    def test_uses_explicit_recipient_when_given(self):
        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as mock_send:
            result = send_monthly_report_task.run(recipient_email="custom@example.com")
        assert result["status"] == "success"
        _, kwargs = mock_send.call_args
        assert kwargs["recipients"] == "custom@example.com"

    def test_uses_admin_monthly_report_template(self):
        """Regression: v1.4 initially used the parent-facing `monthly_report`
        template; fixed to `admin_monthly_report` in the review pass."""
        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as mock_send:
            send_monthly_report_task.run(recipient_email="admin@example.com")
        _, kwargs = mock_send.call_args
        assert kwargs["template_name"] == "admin_monthly_report"

    def test_returns_failed_when_send_fails(self):
        with patch("comms.services.email_service.EmailService.send_email", return_value=False):
            result = send_monthly_report_task.run(recipient_email="a@b.com")
        assert result["status"] == "failed"

    def test_outstanding_is_non_negative(self, completed_payment):
        """After the bug fix, `outstanding = expected - collected` uses one
        date-field (due_date) for both sides so it can never go negative."""
        with patch("comms.services.email_service.EmailService.send_email", return_value=True):
            result = send_monthly_report_task.run(recipient_email="admin@example.com")
        from decimal import Decimal

        assert Decimal(result["outstanding"]) >= Decimal("0.00")
