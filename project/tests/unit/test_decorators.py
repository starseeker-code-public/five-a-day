"""Tests for core.decorators — access-control decorators."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.http import Http404, HttpResponse
from django.test import RequestFactory

from core.decorators import qa_access_required


@pytest.fixture
def rf():
    return RequestFactory()


def _make_request(rf, *, authenticated=False, has_teacher=False):
    """Build a request whose ``user`` mimics an (un)authenticated Teacher.

    ``qa_access_required`` reads ``request.user`` and its reverse ``teacher``
    accessor, so we attach a lightweight stand-in rather than a DB row.
    """
    req = rf.get("/")
    if not authenticated:
        req.user = SimpleNamespace(is_authenticated=False)
    else:
        teacher = object() if has_teacher else None
        req.user = SimpleNamespace(is_authenticated=True, teacher=teacher)
    return req


@qa_access_required
def _view(request):
    return HttpResponse("ok")


class TestQaAccessRequired:
    def test_allows_teacher_in_testing_env(self, rf):
        req = _make_request(rf, authenticated=True, has_teacher=True)
        with patch("core.decorators.settings") as mock_settings:
            mock_settings.IS_TESTING_ENV = True
            response = _view(req)
        assert response.status_code == 200
        assert response.content == b"ok"

    def test_404_when_not_testing_env(self, rf):
        req = _make_request(rf, authenticated=True, has_teacher=True)
        with patch("core.decorators.settings") as mock_settings:
            mock_settings.IS_TESTING_ENV = False
            with pytest.raises(Http404):
                _view(req)

    def test_404_when_authenticated_but_not_a_teacher(self, rf):
        req = _make_request(rf, authenticated=True, has_teacher=False)
        with patch("core.decorators.settings") as mock_settings:
            mock_settings.IS_TESTING_ENV = True
            with pytest.raises(Http404):
                _view(req)

    def test_404_when_anonymous(self, rf):
        req = _make_request(rf, authenticated=False)
        with patch("core.decorators.settings") as mock_settings:
            mock_settings.IS_TESTING_ENV = True
            with pytest.raises(Http404):
                _view(req)
