"""Unit tests for the Twilio SMS service (v1.8)."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from comms.services.sms_service import SmsResult, SmsService, get_sms_service

CR = "\r"
LF = "\n"
BREAK = CR + LF

pytestmark = pytest.mark.django_db


class TestConfiguration:
    def test_unconfigured_when_missing_creds(self):
        with override_settings(
            TWILIO_ACCOUNT_SID="",
            TWILIO_AUTH_TOKEN="secret",
            TWILIO_FROM_NUMBER="+1234567890",
        ):
            assert SmsService().is_configured() is False

    def test_configured_with_all_three(self):
        with override_settings(
            TWILIO_ACCOUNT_SID="AC1",
            TWILIO_AUTH_TOKEN="secret",
            TWILIO_FROM_NUMBER="+1234567890",
        ):
            assert SmsService().is_configured() is True


class TestSend:
    def test_send_returns_failure_when_unconfigured(self):
        with override_settings(TWILIO_ACCOUNT_SID="", TWILIO_AUTH_TOKEN="", TWILIO_FROM_NUMBER=""):
            r = SmsService().send("+34600111222", "hello")
        assert r.success is False
        assert "not configured" in r.error

    def test_send_success(self):
        with override_settings(
            TWILIO_ACCOUNT_SID="AC1",
            TWILIO_AUTH_TOKEN="secret",
            TWILIO_FROM_NUMBER="+1234567890",
        ):
            svc = SmsService()
            fake_client = MagicMock()
            fake_client.messages.create.return_value = MagicMock(sid="SM123")
            with patch.object(svc, "_get_client", return_value=fake_client):
                r = svc.send("+34600111222", "hello")
        assert r.success is True
        assert r.message_sid == "SM123"

    def test_send_wraps_exceptions_as_result(self):
        with override_settings(
            TWILIO_ACCOUNT_SID="AC1",
            TWILIO_AUTH_TOKEN="secret",
            TWILIO_FROM_NUMBER="+1234567890",
        ):
            svc = SmsService()
            with patch.object(svc, "_get_client", side_effect=RuntimeError("api down")):
                r = svc.send("+34600111222", "hi")
        assert r.success is False
        assert "api down" in r.error

    def test_error_is_sanitised_before_reaching_the_result(self):
        """v1.14.6 — the Twilio error is remote input and `SmsResult.error` is
        surfaced in responses, so line terminators must not survive."""
        forged = "denied" + BREAK + "INFO forged log line"
        with override_settings(
            TWILIO_ACCOUNT_SID="AC1",
            TWILIO_AUTH_TOKEN="secret",
            TWILIO_FROM_NUMBER="+1234567890",
        ):
            svc = SmsService()
            with patch.object(svc, "_get_client", side_effect=RuntimeError(forged)):
                r = svc.send("+34600111222", "hi")
        assert r.success is False
        assert CR not in r.error
        assert LF not in r.error
        assert len(r.error.splitlines()) == 1
        assert "denied" in r.error

    def test_long_error_is_length_capped(self):
        with override_settings(
            TWILIO_ACCOUNT_SID="AC1",
            TWILIO_AUTH_TOKEN="secret",
            TWILIO_FROM_NUMBER="+1234567890",
        ):
            svc = SmsService()
            with patch.object(svc, "_get_client", side_effect=RuntimeError("x" * 500)):
                r = svc.send("+34600111222", "hi")
        assert r.error.endswith("...")
        assert len(r.error) == 203

    def test_phone_number_is_sanitised_in_the_log(self, caplog):
        """`to` comes from an admin-typed Parent.phone, so it can't be logged raw."""
        with override_settings(
            TWILIO_ACCOUNT_SID="AC1",
            TWILIO_AUTH_TOKEN="secret",
            TWILIO_FROM_NUMBER="+1234567890",
        ):
            svc = SmsService()
            with patch.object(svc, "_get_client", side_effect=RuntimeError("boom")):
                with caplog.at_level(logging.WARNING):
                    svc.send("+34600111222" + BREAK + "WARNING forged", "hi")
        for record in caplog.records:
            assert len(record.getMessage().splitlines()) == 1


class TestSendToParent:
    def test_skipped_when_not_opted_in(self, parent):
        parent.sms_opt_in = False
        parent.save()
        r = SmsService().send_to_parent(parent, "hi")
        assert r.success is False
        assert "opted in" in r.error

    def test_skipped_when_no_phone(self, parent):
        parent.sms_opt_in = True
        parent.phone = ""
        parent.save()
        r = SmsService().send_to_parent(parent, "hi")
        assert r.success is False
        assert "phone" in r.error

    def test_calls_send_when_all_conditions_met(self, parent):
        parent.sms_opt_in = True
        parent.save()
        with override_settings(TWILIO_ACCOUNT_SID="AC1", TWILIO_AUTH_TOKEN="t", TWILIO_FROM_NUMBER="+1"):
            svc = SmsService()
            with patch.object(svc, "send", return_value=SmsResult(success=True, to=parent.phone)) as mock_send:
                r = svc.send_to_parent(parent, "hi")
        assert r.success is True
        mock_send.assert_called_once_with(parent.phone, "hi")


class TestGetSmsService:
    def test_returns_instance(self):
        assert isinstance(get_sms_service(), SmsService)
