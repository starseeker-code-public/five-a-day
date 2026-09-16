"""Tests for core.middleware — auth middleware edge cases."""

import logging

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def auth_client(client):
    """Client with session authentication set."""
    session = client.session
    session["is_authenticated"] = True
    session["username"] = "testuser"
    session.save()
    client.cookies[client.session.session_key] = session.session_key
    return client


class TestPublicPaths:
    """Verify that public URLs are accessible without authentication."""

    def test_static_not_redirected(self, client):
        # Static files are served by WhiteNoise, but the middleware should not redirect them
        response = client.get("/static/nonexistent.css")
        assert response.status_code != 302

    def test_health_check_public(self, client):
        response = client.get("/health/")
        assert response.status_code == 200

    def test_login_page_public(self, client):
        response = client.get("/login/")
        assert response.status_code == 200


class TestProtectedPaths:
    """Verify that unauthenticated requests are redirected to login."""

    def test_home_redirects_to_login(self, client):
        response = client.get("/")
        assert response.status_code == 302
        assert "/login/" in response["Location"]

    def test_students_redirects_to_login(self, client):
        response = client.get("/students/")
        assert response.status_code == 302
        assert "/login/" in response["Location"]

    def test_api_endpoint_redirects_to_login(self, client):
        response = client.get("/api/history/")
        assert response.status_code == 302
        assert "/login/" in response["Location"]


class TestAuthenticatedAccess:
    """Verify that authenticated requests pass through the middleware."""

    def test_authenticated_home_loads(self, authenticated_client):
        response = authenticated_client.get("/")
        assert response.status_code == 200

    def test_session_without_auth_flag_redirects(self, client):
        # Session exists but is_authenticated is not set
        session = client.session
        session["username"] = "someone"
        session.save()
        response = client.get("/")
        assert response.status_code == 302
        assert "/login/" in response["Location"]


class TestNoHtmlCacheMiddleware:
    """Dynamic HTML must be no-cache so browsers always load current asset hashes."""

    def test_html_response_is_no_cache(self, auth_client):
        response = auth_client.get("/")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")
        assert "no-cache" in response["Cache-Control"]
        assert "no-store" in response["Cache-Control"]

    def test_static_assets_not_touched(self, client):
        # JSON (health check) is not HTML → middleware leaves it alone
        response = client.get("/health/")
        cc = response.get("Cache-Control", "")
        assert "no-store" not in cc


class TestRequestLogMiddleware:
    """Every request gets an id, and the end of it is logged proportionately.

    The bug behind this class: a welcome email failed in production and wrote
    three records from three modules. Nothing linked them, so three alert mails
    arrived looking like three problems, and none of them could name the cause.
    """

    def _run(self, response=None, path="/", **meta):
        """Drive the middleware directly so a status can be forced."""
        from unittest.mock import patch

        from django.http import HttpResponse
        from django.test import RequestFactory

        from core.middleware import RequestLogMiddleware

        request = RequestFactory().get(path, **meta)
        final = response if response is not None else HttpResponse("ok")
        middleware = RequestLogMiddleware(lambda _req: final)
        with patch("core.middleware.logger") as mock_logger:
            result = middleware(request)
        return result, mock_logger

    def _level(self, mock_logger):
        return mock_logger.log.call_args[0][0]

    def test_response_carries_the_id(self, client):
        """Echoed back so a user reporting a problem can quote one string that
        finds every record the request produced."""
        response = client.get("/login/")
        assert response["X-Request-ID"]

    def test_inbound_id_is_honoured(self, client):
        response = client.get("/login/", HTTP_X_REQUEST_ID="upstream-123")
        assert response["X-Request-ID"] == "upstream-123"

    def test_hostile_inbound_id_is_replaced(self, client):
        """The header is client-controlled; a newline in it forges log lines."""
        response = client.get("/login/", HTTP_X_REQUEST_ID="abc\nERROR forged")
        assert "\n" not in response["X-Request-ID"]
        assert response["X-Request-ID"] != "abc\nERROR forged"

    def test_cloud_trace_context_wins_and_the_span_is_dropped(self, client):
        """Cloud Run sets this on the way in; reusing the trace id is what lets
        Cloud Logging nest our entries under its own request log."""
        response = client.get("/login/", HTTP_X_CLOUD_TRACE_CONTEXT="105445aa7843bc8b/1;o=1")
        assert response["X-Request-ID"] == "105445aa7843bc8b"

    def test_cloud_trace_is_bound_for_records_written_during_the_request(self):
        """The formatter builds `logging.googleapis.com/trace` from a record
        field that NOTHING populated until this was plumbed through — the trace
        could never be emitted however the project was configured."""
        import logging as _logging
        from unittest.mock import patch

        from django.http import HttpResponse
        from django.test import RequestFactory

        from core.logging_utils import RequestContextFilter
        from core.middleware import RequestLogMiddleware

        seen = {}

        def get_response(_req):
            record = _logging.LogRecord("core.x", _logging.INFO, "x.py", 1, "m", (), None)
            RequestContextFilter().filter(record)
            seen["trace"] = record.trace_id
            seen["request"] = record.request_id
            return HttpResponse("ok")

        request = RequestFactory().get("/", HTTP_X_CLOUD_TRACE_CONTEXT="105445aa7843bc8b/1;o=1")
        with patch("core.middleware.logger"):
            RequestLogMiddleware(get_response)(request)

        assert seen["trace"] == "105445aa7843bc8b"
        # The same string does both jobs, so one lookup finds the Cloud Run
        # request AND every record the app wrote for it.
        assert seen["request"] == "105445aa7843bc8b"

    def test_a_malformed_trace_header_is_dropped_not_invented(self, client):
        """A fabricated trace would file our records under another request."""
        from core.logging_utils import get_trace_id

        response = client.get("/login/", HTTP_X_CLOUD_TRACE_CONTEXT="not-a-trace/1;o=1")
        assert get_trace_id() == ""
        # The request id still exists — it is ours and never optional.
        assert response["X-Request-ID"]

    def test_context_is_cleared_afterwards(self, client):
        """An id left bound leaks onto the next request the worker serves —
        Gunicorn reuses threads, so this is a real mix-up, not a tidy-up."""
        from core.logging_utils import get_request_id

        client.get("/login/")
        assert get_request_id() == ""

    def test_5xx_returned_by_a_view_is_an_error(self):
        from django.http import HttpResponseServerError

        _response, mock_logger = self._run(response=HttpResponseServerError("boom"))
        assert self._level(mock_logger) == logging.ERROR

    def test_5xx_django_already_logged_is_not_alerted_twice(self):
        """`django.request` logs an unhandled 500 with the traceback, and THAT
        is what mails the admins. A second ERROR here would mean two alerts per
        incident out of two throttle buckets — so the completion line drops to
        the INFO rung: still there, with the timing and the correlation id, but
        it does not alert on its own."""
        from unittest.mock import patch

        from django.http import HttpResponseServerError
        from django.test import RequestFactory

        from core.middleware import RequestLogMiddleware

        request = RequestFactory().get("/")

        def get_response(req):
            req._fad_exception_logged = True
            return HttpResponseServerError("boom")

        with patch("core.middleware.logger") as mock_logger:
            RequestLogMiddleware(get_response)(request)

        assert self._level(mock_logger) == logging.INFO

    def test_slow_request_is_a_warning(self, settings):
        """A request that took nine seconds and SUCCEEDED is invisible in every
        other log this app keeps."""
        settings.SLOW_REQUEST_LOG_MS = 0
        _response, mock_logger = self._run()
        assert self._level(mock_logger) == logging.WARNING

    def test_4xx_is_visible_but_never_alerts(self):
        from django.http import HttpResponseForbidden

        _response, mock_logger = self._run(response=HttpResponseForbidden("no"))
        assert self._level(mock_logger) == logging.INFO

    def test_a_healthy_request_stays_at_debug(self, settings):
        """Production runs at INFO, so an ordinary request logs NOTHING. A line
        per static asset would bury the entries that matter and cost real money
        in Cloud Logging."""
        settings.SLOW_REQUEST_LOG_MS = 3000
        _response, mock_logger = self._run()
        assert self._level(mock_logger) == logging.DEBUG

    def test_the_raw_path_is_never_logged(self):
        """`request.path` is attacker-controlled free text (CodeQL
        py/log-injection) and groups badly; the resolved URL name is a fixed
        vocabulary that aggregates."""
        _response, mock_logger = self._run(path="/login/?next=/%20INJECTED")
        assert "INJECTED" not in str(mock_logger.log.call_args)

    def test_the_url_name_is_logged(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        from django.http import HttpResponse
        from django.test import RequestFactory

        from core.middleware import RequestLogMiddleware

        request = RequestFactory().get("/")

        def get_response(req):
            req.resolver_match = SimpleNamespace(url_name="student_create")
            return HttpResponse("ok")

        with patch("core.middleware.logger") as mock_logger:
            RequestLogMiddleware(get_response)(request)

        assert "student_create" in str(mock_logger.log.call_args)
