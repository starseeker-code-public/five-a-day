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


def _make_request(rf, *, authenticated=False, teacher=None):
    """Build a request whose ``user`` mimics an (un)authenticated Teacher.

    ``qa_access_required`` reads ``request.user`` and its reverse ``teacher``
    accessor (and ``teacher.admin``), so we attach lightweight stand-ins
    rather than DB rows.
    """
    req = rf.get("/")
    if not authenticated:
        req.user = SimpleNamespace(is_authenticated=False)
    else:
        req.user = SimpleNamespace(is_authenticated=True, teacher=teacher)
    return req


@qa_access_required
def _view(request):
    return HttpResponse("ok")


class TestQaAccessRequired:
    def test_allows_admin_teacher_in_testing_env(self, rf):
        req = _make_request(rf, authenticated=True, teacher=SimpleNamespace(admin=True, active=True, tester=False))
        with patch("core.decorators.settings") as mock_settings:
            mock_settings.IS_TESTING_ENV = True
            response = _view(req)
        assert response.status_code == 200
        assert response.content == b"ok"

    def test_404_for_non_admin_teacher(self, rf):
        req = _make_request(rf, authenticated=True, teacher=SimpleNamespace(admin=False, active=True, tester=False))
        with patch("core.decorators.settings") as mock_settings:
            mock_settings.IS_TESTING_ENV = True
            with pytest.raises(Http404):
                _view(req)

    def test_404_for_deactivated_admin_teacher(self, rf):
        """Deactivation is how the academy offboards: the gate must enforce
        `active` itself, not just have the icon hidden by the context processor
        (`may_use_qa_tools` is the one predicate both read)."""
        req = _make_request(rf, authenticated=True, teacher=SimpleNamespace(admin=True, active=False, tester=False))
        with patch("core.decorators.settings") as mock_settings:
            mock_settings.IS_TESTING_ENV = True
            with pytest.raises(Http404):
                _view(req)

    def test_404_for_the_public_tester_account(self, rf):
        """Belt and braces: a tester is `admin=False` and already excluded.

        Asserted with `admin=True` precisely BECAUSE that is the state the
        ordinary clause would wave through. These are the DEV tools — database
        reset and re-seed, the error-email toggle, git internals, and the button
        whose `repository_dispatch` ARMS a production deploy — and the account's
        password is published on a portfolio site, so one mistaken checkbox in
        `/admin/` must not be enough. Saying it in `may_use_qa_tools` rather than
        in `TESTER_ALLOWED_URL_NAMES` also covers all twelve QA endpoints at once
        and hides the icon with the same predicate.
        """
        req = _make_request(rf, authenticated=True, teacher=SimpleNamespace(admin=True, active=True, tester=True))
        with patch("core.decorators.settings") as mock_settings:
            mock_settings.IS_TESTING_ENV = True
            with pytest.raises(Http404):
                _view(req)

    def test_404_when_not_testing_env(self, rf):
        req = _make_request(rf, authenticated=True, teacher=SimpleNamespace(admin=True, active=True, tester=False))
        with patch("core.decorators.settings") as mock_settings:
            mock_settings.IS_TESTING_ENV = False
            with pytest.raises(Http404):
                _view(req)

    def test_404_when_authenticated_but_not_a_teacher(self, rf):
        req = _make_request(rf, authenticated=True, teacher=None)
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
