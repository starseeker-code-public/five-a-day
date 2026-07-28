"""Tests for the management-command wrappers around the Celery Beat tasks.

Production has no Celery Beat process (Cloud Run) — each periodic task is
triggered by Cloud Scheduler running one of these commands in a Cloud Run Job.
The commands are thin: they run the task synchronously via `.apply()`.
"""

import io
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

pytestmark = pytest.mark.django_db


def _eager_result(value):
    mock = MagicMock()
    mock.get.return_value = value
    return mock


class TestSendBirthdayEmailsCommand:
    def test_runs_task_synchronously(self):
        with patch("comms.tasks.send_birthday_emails_task.apply") as mock_apply:
            mock_apply.return_value = _eager_result({"status": "success", "birthdays_found": 0})
            out = io.StringIO()
            call_command("send_birthday_emails", stdout=out)
        mock_apply.assert_called_once()
        assert "Birthday emails" in out.getvalue()


class TestSendPaymentRemindersCommand:
    def test_runs_task_synchronously(self):
        with patch("comms.tasks.send_payment_reminders.apply") as mock_apply:
            mock_apply.return_value = _eager_result({"status": "no_pending_payments", "sent": 0})
            out = io.StringIO()
            call_command("send_payment_reminders", stdout=out)
        mock_apply.assert_called_once()
        assert "Payment reminders" in out.getvalue()


class TestSendMonthlyReportCommand:
    def test_default_recipient_is_none(self):
        with patch("comms.tasks.send_monthly_report_task.apply") as mock_apply:
            mock_apply.return_value = _eager_result({"status": "success"})
            call_command("send_monthly_report", stdout=io.StringIO())
        assert mock_apply.call_args.kwargs["kwargs"] == {"recipient_email": None}

    def test_recipient_override_forwarded(self):
        with patch("comms.tasks.send_monthly_report_task.apply") as mock_apply:
            mock_apply.return_value = _eager_result({"status": "success"})
            call_command("send_monthly_report", "--recipient", "admin@example.com", stdout=io.StringIO())
        assert mock_apply.call_args.kwargs["kwargs"] == {"recipient_email": "admin@example.com"}


class TestSendDueFunFridayEmailsCommand:
    def test_runs_drain_task(self):
        with patch("comms.tasks.send_due_fun_friday_emails_task.apply") as mock_apply:
            mock_apply.return_value = _eager_result({"status": "success", "processed": 0, "sent": 0})
            out = io.StringIO()
            call_command("send_due_fun_friday_emails", stdout=out)
        mock_apply.assert_called_once()
        assert "Fun Friday drain" in out.getvalue()


class TestMaterializeRecurringExpensesCommand:
    def test_default_runs_monthly_task(self):
        with patch("billing.tasks.materialize_recurring_expenses_task.apply") as mock_apply:
            mock_apply.return_value = _eager_result({"status": "success", "created": 0})
            call_command("materialize_recurring_expenses", stdout=io.StringIO())
        assert mock_apply.call_args.kwargs["kwargs"] == {"month": None, "year": None}

    def test_month_year_forwarded(self):
        with patch("billing.tasks.materialize_recurring_expenses_task.apply") as mock_apply:
            mock_apply.return_value = _eager_result({"status": "success", "created": 0})
            call_command("materialize_recurring_expenses", "--month", "3", "--year", "2027", stdout=io.StringIO())
        assert mock_apply.call_args.kwargs["kwargs"] == {"month": 3, "year": 2027}

    def test_daily_flag_runs_daily_task(self):
        with patch("billing.tasks.materialize_recurring_expenses_daily_task.apply") as mock_apply:
            mock_apply.return_value = _eager_result({"status": "success", "created": 0})
            call_command("materialize_recurring_expenses", "--daily", "--date", "2027-03-15", stdout=io.StringIO())
        assert mock_apply.call_args.kwargs["kwargs"] == {"target_date": "2027-03-15"}

    def test_daily_rejects_month_year(self):
        with pytest.raises(CommandError):
            call_command("materialize_recurring_expenses", "--daily", "--month", "3", stdout=io.StringIO())

    def test_monthly_rejects_date(self):
        with pytest.raises(CommandError):
            call_command("materialize_recurring_expenses", "--date", "2027-03-15", stdout=io.StringIO())


class TestCleanupBacklogTasksCommand:
    def test_days_forwarded(self):
        with patch("core.tasks.cleanup_done_backlog_tasks.apply") as mock_apply:
            mock_apply.return_value = _eager_result({"status": "success", "deleted": 0})
            call_command("cleanup_backlog_tasks", "--days", "60", stdout=io.StringIO())
        assert mock_apply.call_args.kwargs["kwargs"] == {"days": 60}

    def test_real_run_deletes_old_done_tasks(self):
        """End-to-end: an old done task is deleted, a fresh one survives."""
        from datetime import timedelta

        from django.utils import timezone

        from core.models import BacklogTask

        old = BacklogTask.objects.create(title="old", description="x", status="done")
        BacklogTask.objects.filter(pk=old.pk).update(updated_at=timezone.now() - timedelta(days=40))
        fresh = BacklogTask.objects.create(title="fresh", description="x", status="done")

        out = io.StringIO()
        call_command("cleanup_backlog_tasks", stdout=out)

        assert not BacklogTask.objects.filter(pk=old.pk).exists()
        assert BacklogTask.objects.filter(pk=fresh.pk).exists()
