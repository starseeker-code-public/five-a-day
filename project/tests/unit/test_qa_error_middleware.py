"""Unit tests for core.middleware.QAErrorEmailMiddleware.

Middleware is invoked directly via RequestFactory; the decorator-style
SimpleAuthMiddleware HTTP-stack tests live in integration/test_middleware.py.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory, override_settings

pytestmark = pytest.mark.django_db


class TestQAErrorEmailMiddleware:
    def _make_request(self, path="/"):
        rf = RequestFactory()
        req = rf.get(path)
        req.session = {"username": "qa_user"}
        return req

    def test_call_passes_through(self):
        from core.middleware import QAErrorEmailMiddleware

        get_response = MagicMock(return_value="response")
        mw = QAErrorEmailMiddleware(get_response)
        result = mw(self._make_request())
        assert result == "response"

    def test_process_exception_disabled_returns_none(self):
        """When QAConfiguration.error_email_enabled is False, middleware no-ops."""
        from core.middleware import QAErrorEmailMiddleware
        from core.models import QAConfiguration

        config = QAConfiguration.get_config()
        config.error_email_enabled = False
        config.save()

        mw = QAErrorEmailMiddleware(lambda r: None)
        result = mw.process_exception(self._make_request(), ValueError("boom"))
        assert result is None

    @override_settings(SUPPORT_EMAIL=None)
    def test_process_exception_no_support_email(self):
        from core.middleware import QAErrorEmailMiddleware
        from core.models import QAConfiguration

        config = QAConfiguration.get_config()
        config.error_email_enabled = True
        config.save()

        mw = QAErrorEmailMiddleware(lambda r: None)
        result = mw.process_exception(self._make_request(), ValueError("boom"))
        assert result is None

    @override_settings(SUPPORT_EMAIL="support@test.local", IS_TESTING_ENV=True)
    def test_process_exception_sends_email(self):
        from core.middleware import QAErrorEmailMiddleware
        from core.models import QAConfiguration

        config = QAConfiguration.get_config()
        config.error_email_enabled = True
        config.save()

        with patch("core.middleware.send_mail") as mock_mail:
            mw = QAErrorEmailMiddleware(lambda r: None)
            mw.process_exception(self._make_request(), ValueError("boom"))
        mock_mail.assert_called_once()

    @override_settings(SUPPORT_EMAIL="support@test.local", IS_TESTING_ENV=True)
    def test_process_exception_send_failure_swallowed(self):
        from core.middleware import QAErrorEmailMiddleware
        from core.models import QAConfiguration

        config = QAConfiguration.get_config()
        config.error_email_enabled = True
        config.save()

        with patch("core.middleware.send_mail", side_effect=RuntimeError("smtp")):
            mw = QAErrorEmailMiddleware(lambda r: None)
            result = mw.process_exception(self._make_request(), ValueError("boom"))
        assert result is None
