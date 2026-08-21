"""Unit tests for the rate_limit decorator (v1.10)."""

import pytest
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse
from django.test import override_settings

from core.rate_limit import _client_ip, rate_limit


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


class TestClientIpValidation:
    """`_client_ip` parses X-Forwarded-For through `ipaddress` (v1.14.5).

    The header is client-supplied and its value becomes both a rate-limit cache
    key and a log record, so anything that isn't a literal address is collapsed
    to "unknown" rather than trusted.
    """

    def _req(self, *, forwarded=None, remote="9.9.9.9"):
        req = HttpRequest()
        req.method = "POST"
        if remote is not None:
            req.META["REMOTE_ADDR"] = remote
        if forwarded is not None:
            req.META["HTTP_X_FORWARDED_FOR"] = forwarded
        return req

    def test_uses_remote_addr_when_no_forwarded_header(self):
        assert _client_ip(self._req()) == "9.9.9.9"

    def test_prefers_first_forwarded_entry(self):
        assert _client_ip(self._req(forwarded="1.2.3.4, 5.6.7.8")) == "1.2.3.4"

    def test_ipv6_is_normalised(self):
        # Same client must not be able to occupy several buckets by varying the
        # textual form of one address.
        assert _client_ip(self._req(forwarded="2001:0db8:0000:0000:0000:0000:0000:0001")) == "2001:db8::1"

    @pytest.mark.parametrize(
        "raw",
        [
            "not-an-ip",
            "1.2.3.4\nINFO forged log line",
            "1.2.3.4\r\nINFO forged log line",
            "999.999.999.999",
            "<script>alert(1)</script>",
        ],
    )
    def test_malformed_forwarded_header_becomes_unknown(self, raw):
        assert _client_ip(self._req(forwarded=raw)) == "unknown"

    def test_malformed_remote_addr_becomes_unknown(self):
        assert _client_ip(self._req(remote="garbage")) == "unknown"

    def test_empty_forwarded_header_falls_back_to_remote_addr(self):
        """An empty header is absent, not malformed."""
        assert _client_ip(self._req(forwarded="", remote="9.9.9.9")) == "9.9.9.9"

    def test_missing_both_becomes_unknown(self):
        assert _client_ip(self._req(remote=None)) == "unknown"
