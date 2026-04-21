"""Tests for core.decorators — access-control decorators."""

from unittest.mock import patch

import pytest
from django.http import Http404, HttpResponse
from django.test import RequestFactory

from core.decorators import qa_access_required


@pytest.fixture
def rf():
    return RequestFactory()


def _make_request(rf, username=None):
    req = rf.get("/")
    # Attach a dict-like session
    req.session = {}
    if username is not None:
        req.session["username"] = username
    return req


@qa_access_required
def _view(request):
    return HttpResponse("ok")


class TestQaAccessRequired:
    def test_allows_when_all_conditions_met(self, rf):
        req = _make_request(rf, username="qa_user")
        with patch("core.decorators.settings") as mock_settings:
            mock_settings.IS_TESTING_ENV = True
            mock_settings.QA_TESTING_USERNAME = "qa_user"
            response = _view(req)
        assert response.status_code == 200
        assert response.content == b"ok"

    def test_404_when_not_testing_env(self, rf):
        req = _make_request(rf, username="qa_user")
        with patch("core.decorators.settings") as mock_settings:
            mock_settings.IS_TESTING_ENV = False
            mock_settings.QA_TESTING_USERNAME = "qa_user"
            with pytest.raises(Http404):
                _view(req)

    def test_404_when_no_qa_username_configured(self, rf):
        req = _make_request(rf, username="qa_user")
        with patch("core.decorators.settings") as mock_settings:
            mock_settings.IS_TESTING_ENV = True
            mock_settings.QA_TESTING_USERNAME = ""
            with pytest.raises(Http404):
                _view(req)

    def test_404_when_session_user_does_not_match(self, rf):
        req = _make_request(rf, username="someone_else")
        with patch("core.decorators.settings") as mock_settings:
            mock_settings.IS_TESTING_ENV = True
            mock_settings.QA_TESTING_USERNAME = "qa_user"
            with pytest.raises(Http404):
                _view(req)

    def test_404_when_no_session_user(self, rf):
        req = _make_request(rf)
        with patch("core.decorators.settings") as mock_settings:
            mock_settings.IS_TESTING_ENV = True
            mock_settings.QA_TESTING_USERNAME = "qa_user"
            with pytest.raises(Http404):
                _view(req)
