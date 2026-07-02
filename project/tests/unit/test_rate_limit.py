"""Unit tests for the rate_limit decorator (v1.10)."""

import pytest
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse
from django.test import override_settings

from core.rate_limit import rate_limit


@pytest.fixture(autouse=True)
def _clear_cache_and_enable_rl():
    cache.clear()
    # Rate limiter is disabled globally in tests via settings_test.py;
    # re-enable it just for this file so we can actually exercise the branch.
    with override_settings(RATELIMIT_ENABLE=True):
        yield
    cache.clear()


def _make_request(method="POST", ip="1.2.3.4"):
    req = HttpRequest()
    req.method = method
    req.META["REMOTE_ADDR"] = ip
    return req


@rate_limit("test_scope", limit=3, window_seconds=60)
def _endpoint(request):
    return HttpResponse("ok")


class TestRateLimit:
    def test_allows_below_limit(self):
        for _ in range(3):
            assert _endpoint(_make_request()).status_code == 200

    def test_blocks_when_exceeded(self):
        for _ in range(3):
            _endpoint(_make_request())
        response = _endpoint(_make_request())
        assert response.status_code == 429

    def test_get_requests_never_throttled(self):
        for _ in range(10):
            assert _endpoint(_make_request(method="GET")).status_code == 200

    def test_per_ip_isolation(self):
        for _ in range(3):
            _endpoint(_make_request(ip="1.1.1.1"))
        # Different IP still allowed
        assert _endpoint(_make_request(ip="2.2.2.2")).status_code == 200
