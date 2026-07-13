"""Unit tests for the v1.8 SMS Celery task."""

from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from comms.tasks import send_payment_reminder_sms_task

pytestmark = pytest.mark.django_db


class TestSendPaymentReminderSmsTask:
    def test_returns_error_when_payment_missing(self):
        result = send_payment_reminder_sms_task.run(999_999)
        assert result["status"] == "error"

    def test_skipped_when_no_parent(self, adult_student, active_enrollment):
        from datetime import date
        from decimal import Decimal

        from billing.models import Payment

        p = Payment.objects.create(
            student=adult_student,
            parent=None,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("60.00"),
            payment_status="pending",
            due_date=date.today(),
            concept="Test",
        )
        r = send_payment_reminder_sms_task.run(p.id)
        assert r["status"] == "skipped"

    def test_skipped_when_sms_unconfigured(self, pending_payment):
        with override_settings(TWILIO_ACCOUNT_SID="", TWILIO_AUTH_TOKEN="", TWILIO_FROM_NUMBER=""):
            r = send_payment_reminder_sms_task.run(pending_payment.id)
        assert r["status"] == "skipped"

    def test_sends_when_all_configured(self, pending_payment):
        pending_payment.parent.sms_opt_in = True
        pending_payment.parent.save()
        with override_settings(TWILIO_ACCOUNT_SID="AC", TWILIO_AUTH_TOKEN="t", TWILIO_FROM_NUMBER="+1"):
            fake = MagicMock()
            fake.messages.create.return_value = MagicMock(sid="SM123")
            with patch("comms.services.sms_service.SmsService._get_client", return_value=fake):
                r = send_payment_reminder_sms_task.run(pending_payment.id)
        assert r["success"] is True
        assert r["message_sid"] == "SM123"
