"""Sanity checks for the Celery Beat schedule (v1.4)."""

import pytest

from project.celery import app


class TestBeatSchedule:
    def test_schedule_contains_all_v14_periodic_tasks(self):
        schedule = app.conf.beat_schedule
        # Pre-existing
        assert "send-birthday-emails-daily" in schedule
        assert "send-payment-reminders-weekly" in schedule
        # v1.4 additions
        assert "generate-monthly-payments" in schedule
        assert "send-monthly-report" in schedule
        # Fun Friday drain (persisted scheduled sends)
        assert "send-due-fun-friday-emails" in schedule

    def test_fun_friday_drain_runs_daily_at_1430(self):
        entry = app.conf.beat_schedule["send-due-fun-friday-emails"]
        assert entry["task"] == "comms.tasks.send_due_fun_friday_emails_task"
        schedule = entry["schedule"]
        assert 14 in schedule.hour
        assert 30 in schedule.minute
        # daily — no day-of-month restriction
        assert len(schedule.day_of_month) == 31

    def test_generate_monthly_payments_runs_on_day_1(self):
        entry = app.conf.beat_schedule["generate-monthly-payments"]
        assert entry["task"] == "billing.tasks.generate_monthly_payments_task"
        # Crontab objects expose the parsed schedule via _orig_day_of_month or day_of_month
        schedule = entry["schedule"]
        # crontab.day_of_month is a set
        assert 1 in schedule.day_of_month

    def test_monthly_report_runs_on_day_28(self):
        entry = app.conf.beat_schedule["send-monthly-report"]
        assert entry["task"] == "comms.tasks.send_monthly_report_task"
        assert 28 in entry["schedule"].day_of_month

    def test_timezone_is_europe_madrid(self):
        assert app.conf.timezone == "Europe/Madrid"


class TestTaskDiscovery:
    @pytest.mark.parametrize(
        "task_name",
        [
            "billing.tasks.generate_monthly_payments_task",
            "comms.tasks.send_monthly_report_task",
            "comms.tasks.send_birthday_emails_task",
            "comms.tasks.send_payment_reminders",
            "comms.tasks.send_due_fun_friday_emails_task",
        ],
    )
    def test_task_is_registered(self, task_name):
        # Force autodiscover just to be safe in the test env.
        app.autodiscover_tasks(force=True)
        assert task_name in app.tasks
