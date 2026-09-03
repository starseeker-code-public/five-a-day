"""Unit tests for the v1.4 Celery Beat tasks."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone

from billing.models import Expense
from billing.tasks import (
    generate_monthly_payments_task,
    materialize_recurring_expenses_daily_task,
)
from comms.tasks import send_birthday_emails_task, send_monthly_report_task, send_payment_reminders
from students.models import Student

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


class TestMaterializeRecurringExpensesDailyTask:
    """The daily weekly/yearly materialiser (Beat 06:15).

    `test_beat_commands.py` patches `.apply()` to assert the command wiring, so
    the task body itself never runs there; `test_expenses.py` exercises the
    service directly. This class covers the task, which is what Beat and the
    Cloud Run Job actually invoke.
    """

    @staticmethod
    def _weekly_template(weekdays: str) -> Expense:
        return Expense.objects.create(
            description="Limpieza semanal",
            category="other",
            amount=Decimal("30.00"),
            expense_date=date(2026, 1, 1),
            is_recurring=True,
            recurring_frequency="weekly",
            recurring_weekdays=weekdays,
        )

    def test_target_date_string_is_parsed_and_used(self):
        # 2026-03-02 is a Monday (weekday 0).
        tpl = self._weekly_template("0")
        result = materialize_recurring_expenses_daily_task.run(target_date="2026-03-02")
        assert result["status"] == "success"
        assert result["created"] == 1
        assert result["date"] == "2026-03-02"
        assert (
            Expense.objects.filter(generated_from=tpl, expense_date=date(2026, 3, 2), is_recurring=False).count() == 1
        )

    def test_non_matching_weekday_creates_nothing(self):
        self._weekly_template("0")  # Mondays only
        result = materialize_recurring_expenses_daily_task.run(target_date="2026-03-03")  # Tuesday
        assert result["created"] == 0
        assert not Expense.objects.filter(is_recurring=False).exists()

    def test_rerun_is_idempotent(self):
        """Beat and the Cloud Run Job can both fire on the same day — a second
        run must not duplicate the generated row."""
        self._weekly_template("0")
        first = materialize_recurring_expenses_daily_task.run(target_date="2026-03-02")
        second = materialize_recurring_expenses_daily_task.run(target_date="2026-03-02")
        assert first["created"] == 1
        assert second["created"] == 0
        assert Expense.objects.filter(expense_date=date(2026, 3, 2), is_recurring=False).count() == 1

    def test_defaults_to_today_when_no_target_date(self):
        result = materialize_recurring_expenses_daily_task.run()
        assert result["status"] == "success"
        assert result["date"] == date.today().isoformat()


class TestSendMonthlyReportTask:
    # Every test here pins BOTH SUPPORT_EMAIL and DEFAULT_FROM_EMAIL. The
    # default recipient set is built from the two of them, and
    # `DEFAULT_FROM_EMAIL` is `os.getenv("EMAIL_HOST_USER", "")` — so a test
    # that leaves it ambient passes or fails depending on whether the machine
    # running it has a mail account in its .env. That is what made
    # `test_skips_when_no_recipient` green in CI and red on a dev box.
    def test_skips_when_nobody_is_configured(self):
        # Both empty is the only way to reach the skip branch: an unset
        # SUPPORT_EMAIL alone still leaves the academy's own Gmail address.
        with override_settings(SUPPORT_EMAIL=None, DEFAULT_FROM_EMAIL=""):
            result = send_monthly_report_task.run()
        assert result["status"] == "skipped"

    def test_falls_back_to_the_from_address_when_support_is_unset(self):
        with override_settings(SUPPORT_EMAIL=None, DEFAULT_FROM_EMAIL="academy@example.com"):
            with patch("comms.services.email_service.EmailService.send_email", return_value=True) as mock_send:
                result = send_monthly_report_task.run()
        assert result["status"] == "success"
        _, kwargs = mock_send.call_args
        assert kwargs["recipients"] == ["academy@example.com"]

    def test_sends_when_recipient_configured(self, pending_payment, completed_payment):
        with override_settings(SUPPORT_EMAIL="admin@example.com", DEFAULT_FROM_EMAIL="academy@example.com"):
            with patch("comms.services.email_service.EmailService.send_email", return_value=True) as mock_send:
                result = send_monthly_report_task.run()
        assert result["status"] == "success"
        assert float(result["expected"]) >= 0
        assert float(result["collected"]) >= 0
        # One send, two inboxes: the academy reads this at the support address
        # and in its own Gmail account.
        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        assert kwargs["recipients"] == ["admin@example.com", "academy@example.com"]

    def test_deduplicates_when_both_addresses_match(self):
        with override_settings(SUPPORT_EMAIL="same@example.com", DEFAULT_FROM_EMAIL="same@example.com"):
            with patch("comms.services.email_service.EmailService.send_email", return_value=True) as mock_send:
                send_monthly_report_task.run()
        _, kwargs = mock_send.call_args
        assert kwargs["recipients"] == ["same@example.com"]

    def test_uses_explicit_recipient_when_given(self):
        # `--recipient` is an override, not an addition: that one run goes only
        # where it was pointed, never to the configured inboxes as well.
        with override_settings(SUPPORT_EMAIL="admin@example.com", DEFAULT_FROM_EMAIL="academy@example.com"):
            with patch("comms.services.email_service.EmailService.send_email", return_value=True) as mock_send:
                result = send_monthly_report_task.run(recipient_email="custom@example.com")
        assert result["status"] == "success"
        _, kwargs = mock_send.call_args
        assert kwargs["recipients"] == ["custom@example.com"]

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


class TestFanOutSurvivesAFailingItem:
    """Production has no broker: `.delay()` runs INLINE and propagates
    (`CELERY_TASK_ALWAYS_EAGER` + `CELERY_TASK_EAGER_PROPAGATES`), so a bare
    fan-out loop stopped dead on its first bad item — measured: item 0 ran,
    items 1-3 never did. `_dispatch` is what keeps the batch alive.
    """

    def test_birthday_batch_continues_past_a_failing_student(self, db, group):
        today = timezone.localdate()
        for i in range(3):
            Student.objects.create(
                first_name=f"Cumple{i}",
                last_name="X",
                group=group,
                birth_date=today.replace(year=2015),
                active=True,
            )

        sent = []

        def flaky(student_id):
            sent.append(student_id)
            if len(sent) == 1:
                raise RuntimeError("bad address")

        with patch("comms.tasks.send_birthday_email_task") as task:
            task.delay.side_effect = flaky
            result = send_birthday_emails_task.apply().get()

        assert len(sent) == 3, "the failure must not stop the remaining students"
        assert result["tasks_queued"] == 2
        assert result["tasks_failed"] == 1

    def test_sms_failure_cannot_suppress_reminder_emails(self, db, student, parent, active_enrollment):
        """The SMS dispatch used to sit inside the loop BEFORE the bulk email
        send, so one Twilio failure raised first and no reminder email went out
        at all — the comment on that line claimed the opposite."""
        from datetime import timedelta as td

        from billing.models import Payment as P

        parent.sms_opt_in = True
        parent.phone = "600000001"
        parent.save()
        P.objects.create(
            student=student,
            parent=parent,
            enrollment=active_enrollment,
            payment_type="monthly",
            amount=Decimal("54.00"),
            payment_status="pending",
            due_date=date.today() + td(days=3),
            concept="Mensualidad",
        )

        with (
            patch("comms.tasks.send_payment_reminder_sms_task") as sms,
            patch("comms.services.email_service.EmailService.send_bulk_emails") as bulk,
        ):
            sms.delay.side_effect = RuntimeError("twilio down")
            bulk.return_value = {"sent": 1, "failed": 0}
            result = send_payment_reminders.apply().get()

        bulk.assert_called_once()
        assert result["sent"] == 1, "email must go out regardless of SMS"
        assert result["sms_failed"] == 1
        assert result["sms_queued"] == 0
