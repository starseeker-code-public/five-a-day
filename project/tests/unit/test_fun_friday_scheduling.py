"""Tests for persisted Fun Friday scheduled sends (FunFridayScheduledSend + drain task)."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from comms.tasks import send_due_fun_friday_emails_task
from core.models import FunFridayScheduledSend

pytestmark = pytest.mark.django_db


def _make_scheduled(scheduled_for, **overrides):
    defaults = {
        "recipients": ["parent@example.com"],
        "day_name": "Viernes",
        "day_number": 17,
        "month": "abril",
        "start_time": "17:00",
        "end_time": "18:30",
        "activity_description": "<b>Crafts</b>",
        "minimum_age": 5,
        "maximum_age": 12,
        "meeting_point": "Main entrance",
        "scheduled_for": scheduled_for,
    }
    defaults.update(overrides)
    return FunFridayScheduledSend.objects.create(**defaults)


class TestFunFridayScheduledSendModel:
    def test_is_due_for_past_unsent(self):
        row = _make_scheduled(timezone.now() - timedelta(minutes=1))
        assert row.is_due

    def test_not_due_when_in_the_future(self):
        row = _make_scheduled(timezone.now() + timedelta(days=1))
        assert not row.is_due

    def test_not_due_when_already_sent(self):
        row = _make_scheduled(timezone.now() - timedelta(days=1), sent_at=timezone.now())
        assert not row.is_due

    def test_str_shows_status(self):
        row = _make_scheduled(timezone.now() + timedelta(days=1))
        assert "pending" in str(row)
        row.sent_at = timezone.now()
        assert "sent" in str(row)


class TestSendDueFunFridayEmailsTask:
    def test_sends_due_rows_and_marks_sent(self):
        due = _make_scheduled(timezone.now() - timedelta(minutes=5))
        with patch(
            "comms.tasks._send_fun_friday_batch", return_value={"status": "success", "sent": 1, "total": 1}
        ) as mock_batch:
            result = send_due_fun_friday_emails_task.apply().get()
        assert result == {"status": "success", "processed": 1, "sent": 1, "failed": 0}
        mock_batch.assert_called_once()
        assert mock_batch.call_args.kwargs["recipients"] == ["parent@example.com"]
        due.refresh_from_db()
        assert due.sent_at is not None

    def test_one_failing_row_does_not_abort_the_drain(self):
        """A row is CLAIMED before sending, so an exception used to abort the
        loop and leave every later due row claimed but unsent."""
        first = _make_scheduled(timezone.now() - timedelta(minutes=10))
        second = _make_scheduled(timezone.now() - timedelta(minutes=5))

        calls = []

        def flaky(**kwargs):
            calls.append(kwargs["recipients"])
            if len(calls) == 1:
                raise RuntimeError("smtp down")
            return {"status": "success", "sent": 1, "total": 1}

        with patch("comms.tasks._send_fun_friday_batch", side_effect=flaky):
            result = send_due_fun_friday_emails_task.apply().get()

        assert len(calls) == 2, "the drain must continue past the failing row"
        assert result["failed"] == 1
        assert result["processed"] == 1
        # Both stay claimed: the batch mails parents one at a time and may have
        # delivered some already, so releasing the claim would re-mail them.
        for row in (first, second):
            row.refresh_from_db()
            assert row.sent_at is not None

    def test_skips_future_rows(self):
        future = _make_scheduled(timezone.now() + timedelta(days=2))
        with patch("comms.tasks._send_fun_friday_batch") as mock_batch:
            result = send_due_fun_friday_emails_task.apply().get()
        assert result["processed"] == 0
        assert not mock_batch.called
        future.refresh_from_db()
        assert future.sent_at is None

    def test_idempotent_never_resends(self):
        _make_scheduled(timezone.now() - timedelta(minutes=5))
        with patch(
            "comms.tasks._send_fun_friday_batch", return_value={"status": "success", "sent": 1, "total": 1}
        ) as mock_batch:
            send_due_fun_friday_emails_task.apply().get()
            result = send_due_fun_friday_emails_task.apply().get()
        assert mock_batch.call_count == 1
        assert result["processed"] == 0

    def test_real_send_uses_email_service(self):
        """End-to-end drain through the real batch sender (locmem email backend)."""
        _make_scheduled(timezone.now() - timedelta(minutes=5))
        result = send_due_fun_friday_emails_task.apply().get()
        assert result["processed"] == 1
