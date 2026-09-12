"""Unit tests for the rate_limit decorator (v1.10)."""

import time

import pytest
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse
from django.test import override_settings

from core.rate_limit import _claim_slot, _client_ip, _slot_key, rate_limit


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

    def test_uses_remote_addr_when_no_forwarded_header(self, settings):
        settings.TRUSTED_PROXY_COUNT = 1
        assert _client_ip(self._req()) == "9.9.9.9"

    def test_default_outside_production_ignores_forwarded_header(self):
        # settings_test inherits a non-production DJANGO_ENV, so the DEFAULT
        # must be 0 trusted proxies: the QA VM and the dev stack have no proxy,
        # and trusting one hop there let a rotating X-Forwarded-For defeat
        # every credential throttle.
        assert _client_ip(self._req(forwarded="1.2.3.4")) == "9.9.9.9"

    def test_reads_the_hop_our_proxy_appended_not_the_client_prefix(self, settings):
        """With one trusted proxy we take the LAST entry, not the first.

        A proxy APPENDS the address it saw, so the rightmost entries are the
        ones our own infrastructure wrote and everything to the left is
        client-supplied. Reading `split(",")[0]` let an attacker prepend a
        value and land in a fresh rate-limit bucket on every request, which
        made the login throttle a no-op.

        (TRUSTED_PROXY_COUNT is set explicitly: the settings default is now
        environment-aware — 0 outside production, where no proxy exists.)
        """
        settings.TRUSTED_PROXY_COUNT = 1
        assert _client_ip(self._req(forwarded="1.2.3.4, 5.6.7.8")) == "5.6.7.8"

    def test_spoofed_prefix_cannot_rotate_the_bucket(self, settings):
        settings.TRUSTED_PROXY_COUNT = 1
        real = "203.0.113.9"
        seen = {
            _client_ip(self._req(forwarded=f"{spoof}, {real}"))
            for spoof in ("10.0.0.1", "10.0.0.2", "10.0.0.3", "198.51.100.7")
        }
        assert seen == {real}, "rotating the client-supplied prefix must not change the bucket"

    def test_zero_trusted_proxies_ignores_forwarded_header(self, settings):
        settings.TRUSTED_PROXY_COUNT = 0
        req = self._req(forwarded="1.2.3.4")
        req.META["REMOTE_ADDR"] = "198.51.100.1"
        assert _client_ip(req) == "198.51.100.1"

    def test_two_trusted_proxies_reads_two_from_the_right(self, settings):
        settings.TRUSTED_PROXY_COUNT = 2
        assert _client_ip(self._req(forwarded="1.2.3.4, 203.0.113.5, 10.0.0.1")) == "203.0.113.5"

    def test_short_chain_falls_back_to_remote_addr(self, settings):
        """Fewer hops than configured means the header didn't come from our
        proxies — trust REMOTE_ADDR instead of a client-supplied value."""
        settings.TRUSTED_PROXY_COUNT = 3
        req = self._req(forwarded="1.2.3.4")
        req.META["REMOTE_ADDR"] = "198.51.100.1"
        assert _client_ip(req) == "198.51.100.1"

    def test_ipv6_is_normalised(self, settings):
        # Same client must not be able to occupy several buckets by varying the
        # textual form of one address.
        settings.TRUSTED_PROXY_COUNT = 1
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
    def test_malformed_forwarded_header_becomes_unknown(self, raw, settings):
        settings.TRUSTED_PROXY_COUNT = 1
        assert _client_ip(self._req(forwarded=raw)) == "unknown"

    def test_malformed_remote_addr_becomes_unknown(self):
        assert _client_ip(self._req(remote="garbage")) == "unknown"

    def test_empty_forwarded_header_falls_back_to_remote_addr(self, settings):
        """An empty header is absent, not malformed."""
        settings.TRUSTED_PROXY_COUNT = 1
        assert _client_ip(self._req(forwarded="", remote="9.9.9.9")) == "9.9.9.9"

    def test_missing_both_becomes_unknown(self):
        assert _client_ip(self._req(remote=None)) == "unknown"


class TestSlotClaimIsNotLostUnderConcurrency:
    """The counter this replaced was `cache.add(key, 0)` + `cache.incr(key)`.

    `DatabaseCache` — the backend production runs, via `CACHE_DB=True` — does
    not override `incr`, so it inherits `BaseCache.incr`, a plain get-then-set.
    Two concurrent requests both read N and both write N+1 and one attempt
    vanishes, so the real ceiling was `limit` plus the worker concurrency.
    Slots are claimed with `add()`, which is a primary-key INSERT: exactly one
    racing caller can win a given slot, so no attempt can be lost.
    """

    def test_exactly_limit_requests_pass_then_the_window_closes(self):
        assert [_endpoint(_make_request()).status_code for _ in range(5)] == [200, 200, 200, 429, 429]

    def test_a_lost_update_in_incr_can_no_longer_help(self, monkeypatch):
        """Simulates the backend flaw directly: make `incr` a no-op. The old
        implementation counted through it and let everything past; the slot
        claim never calls it."""
        monkeypatch.setattr(cache, "incr", lambda *a, **kw: 0)

        codes = [_endpoint(_make_request(ip="7.7.7.7")).status_code for _ in range(5)]
        assert codes.count(200) == 3
        assert codes[-1] == 429

    def test_each_attempt_takes_a_distinct_slot(self):
        for _ in range(3):
            _endpoint(_make_request(ip="8.8.8.8"))

        window = int(time.time()) // 60
        held = [i for i in range(3) if cache.get(_slot_key("test_scope", "8.8.8.8", window, i)) is not None]
        assert held == [0, 1, 2], "three attempts must occupy three separate slots"


class TestFailsOpenWhenTheCacheIsUnreachable:
    """Deliberate: locking the academy out of their own admin is worse than
    briefly losing a defence-in-depth control. `add()` swallows DatabaseError
    and returns False, which is indistinguishable from a full window — so the
    probe that separates them is what keeps this behaviour."""

    def test_a_raising_cache_lets_the_request_through(self, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("cache down")

        monkeypatch.setattr(cache, "add", boom)
        assert _endpoint(_make_request(ip="5.5.5.5")).status_code == 200

    def test_add_failing_silently_is_treated_as_an_outage_not_a_full_window(self, monkeypatch):
        """The DatabaseCache failure mode: `add` returns False for every slot
        while nothing was actually stored."""
        monkeypatch.setattr(cache, "add", lambda *a, **kw: False)
        assert _claim_slot("test_scope", "6.6.6.6", 3, 60) is None
        assert _endpoint(_make_request(ip="6.6.6.6")).status_code == 200

    def test_a_genuinely_full_window_still_throttles(self):
        """The other side of that probe: slots really are taken, so this must
        be a 429 and not a fail-open."""
        for _ in range(3):
            _endpoint(_make_request(ip="4.4.4.4"))
        assert _claim_slot("test_scope", "4.4.4.4", 3, 60) is False
