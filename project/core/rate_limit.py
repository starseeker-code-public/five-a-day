"""
Simple session + cache rate limiter (v1.10).

Used to throttle login attempts (5/min per IP), the password-reset request, the
2FA verify step and the parent-portal magic link.

The CACHE BACKEND decides whether any of this is real. Django's default
LocMemCache is per-process, so on Cloud Run (4 Gunicorn workers x maxScale 2)
"5 per minute" was up to 40 across eight independent counters. See
settings.CACHES: production sets CACHE_DB=True to use the shared PostgreSQL
cache table. Do not revert to a per-process cache and do not point CACHE_URL at
an unreachable host.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_SECONDS = 60
DEFAULT_LIMIT = 5


def _client_ip(request) -> str:
    """Best-effort client IP, validated to a literal address.

    `X-Forwarded-For` is client-supplied and this value becomes both a cache
    key and a log record, so it is parsed through `ipaddress` rather than
    trusted verbatim: that rejects anything containing a separator, a line
    break, or arbitrary text, and normalises the representation so the same
    client can't occupy several rate-limit buckets.

    We read the entry `TRUSTED_PROXY_COUNT` hops from the RIGHT, not the
    leftmost one. A proxy APPENDS the address it saw, so the rightmost entries
    are the ones our own infrastructure wrote and the leftmost is whatever the
    client sent. Taking `split(",")[0]` meant an attacker could prepend a fake
    `X-Forwarded-For` and land in a brand-new rate-limit bucket on every single
    request, which made the login throttle a no-op (verified: 12 attempts with
    rotating values, 0 throttled).

    `TRUSTED_PROXY_COUNT` defaults to 1 (Cloud Run / a single reverse proxy).
    Set it to 0 when the app is reached directly, so XFF is ignored entirely.
    """
    trusted = getattr(settings, "TRUSTED_PROXY_COUNT", 1)
    raw = request.META.get("REMOTE_ADDR", "")

    if trusted > 0:
        fwd = request.META.get("HTTP_X_FORWARDED_FOR")
        if fwd:
            hops = [h.strip() for h in fwd.split(",") if h.strip()]
            # The client-controlled prefix can be any length; index from the
            # right so extra spoofed hops shift the window past them, never
            # into them.
            if len(hops) >= trusted:
                raw = hops[-trusted]

    try:
        return ipaddress.ip_address(raw).compressed
    except ValueError:
        return "unknown"


def _slot_key(scope: str, ip: str, window: int, index: int) -> str:
    return f"rl:{scope}:{ip}:{window}:{index}"


def _claim_slot(scope: str, ip: str, limit: int, window_seconds: int) -> bool | None:
    """Try to claim one of `limit` slots for this client in the current window.

    Returns True when a slot was claimed, False when the window is full, and
    None when the cache could not answer (the caller then fails OPEN).

    Why slots rather than a counter: the previous version did `cache.add(key, 0)`
    then `cache.incr(key)` and its comment claimed that closed the check-then-set
    race. It does — on memcached and Redis. Production runs neither. With
    `CACHE_DB=True` the backend is `DatabaseCache`, which does NOT override
    `incr`, so it inherits `BaseCache.incr` — a plain get-then-set. Two
    concurrent requests both read N and both write N+1, and the attempt is lost.
    Under a burst the counter lags by roughly the number of parallel workers, so
    the real ceiling was `limit` plus concurrency rather than `limit`.

    `add()` has no such problem on any backend: it is an INSERT against the
    cache table's primary key, and exactly one racing caller wins. Claiming a
    distinct key per attempt turns the counter into `limit` one-shot slots, so
    the count cannot be lost. Costs one query for the first attempt in a window
    and at most `limit` for a throttled one.
    """
    window = int(time.time()) // window_seconds
    try:
        for index in range(limit):
            # Slots outlive their window so a clock-granularity edge cannot free
            # one early; the window number is in the key, so a stale slot is
            # never consulted again.
            if cache.add(_slot_key(scope, ip, window, index), 1, timeout=window_seconds * 2):
                return True

        # `add()` returning False is ambiguous on the database backend: it is
        # what a taken slot looks like AND what a DatabaseError looks like,
        # because `DatabaseCache._base_set` swallows the exception and returns
        # False. `get()` does not swallow, so it separates "window is full"
        # from "cache is unreachable" — without which a dead cache would lock
        # every user out instead of failing open.
        if cache.get(_slot_key(scope, ip, window, 0)) is None:
            return None
        return False
    except Exception:
        return None


def rate_limit(
    scope: str,
    *,
    limit: int = DEFAULT_LIMIT,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    count_methods: tuple[str, ...] = ("POST",),
):
    """
    Decorator: throttle a view to `limit` requests per `window_seconds` per
    client IP. Sends a 429 with a friendly message when exceeded.

    `count_methods` defaults to `("POST",)` so we don't throttle normal GETs
    of a page. For endpoints like magic-link verify (GET) or brute-forceable
    token URLs, pass `("GET", "POST")` (or just `("GET",)`).
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapper(request, *args, **kwargs):
            if request.method not in count_methods:
                return view_func(request, *args, **kwargs)

            # Bypass in the test suite so the shared cache doesn't leak across
            # test cases. Django's DEBUG stays True in dev, but pytest-django
            # sets it too — so we opt out via an explicit RATELIMIT_ENABLE flag
            # instead.
            if not getattr(settings, "RATELIMIT_ENABLE", True):
                return view_func(request, *args, **kwargs)

            ip = _client_ip(request)
            claimed = _claim_slot(scope, ip, limit, window_seconds)

            if claimed is None:
                # The cache is a REAL external dependency (Redis, or the
                # PostgreSQL cache table — see settings.CACHES), so it can be
                # unreachable, and it sits on the login path.
                #
                # Fail OPEN, loudly. Locking the academy out of the only admin
                # interface for their live business is worse than temporarily
                # losing a defence-in-depth control, and this is not the primary
                # authentication check — the password and 2FA are. The ERROR log
                # is the point: a silently disabled throttle is how the
                # per-process LocMemCache problem went unnoticed for so long.
                logger.error(
                    "Rate limiter cache unavailable — scope=%s is UNTHROTTLED for this request",
                    scope,
                    exc_info=True,
                )
                return view_func(request, *args, **kwargs)

            if not claimed:
                logger.info("rate limit exceeded: scope=%s ip=%s limit=%s", scope, ip, limit)
                return HttpResponse(
                    "⚠️ Demasiados intentos. Prueba de nuevo en un minuto.",
                    status=429,
                    content_type="text/plain; charset=utf-8",
                )
            return view_func(request, *args, **kwargs)

        return _wrapper

    return decorator


class ErrorAlertThrottleFilter(logging.Filter):
    """Let at most one alert per distinct error site through per window.

    Wired to the `mail_admins` handler in `settings.LOGGING` (production only).
    `AdminEmailHandler` has no throttle of its own, and the failure mode of
    error email is not "too few": a 500 on a page a Cloud Scheduler job hits, or
    one bad row in a loop, produces one mail per occurrence, the inbox is muted
    within a day, and the alerting is then worse than none because it is
    believed to be working.

    Deliberately IN-PROCESS (a dict and a monotonic clock) rather than
    cache-backed like `rate_limit` above:

    * this runs while handling an error, and the cache is the database in
      production — an alert about the database being unreachable must not
      depend on the database being reachable;
    * per-process is enough. The upper bound becomes one mail per site per
      window per worker: 4 Gunicorn workers x maxScale 2 = at most 8, versus
      thousands. For the rate LIMITER that multiplication was the bug; for an
      alert it is an acceptable constant.

    Keyed on the logging call SITE (logger + module + line), not on the
    formatted message, so a per-request id or a student name in the text cannot
    defeat it — that is exactly the shape that spams.
    """

    #: One mail per site per 15 minutes. Long enough to survive a burst, short
    #: enough that a problem lasting an afternoon reminds you it is still there.
    WINDOW_SECONDS = 900
    #: Bound the bookkeeping. An error storm from many distinct sites must not
    #: grow this dict without limit inside a process that is already unhealthy.
    MAX_TRACKED = 512

    def __init__(self, name: str = "") -> None:
        super().__init__(name)
        self._seen: dict[tuple, float] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        key = (record.name, record.levelno, record.module, record.lineno)
        now = time.monotonic()
        last = self._seen.get(key)
        if last is not None and now - last < self.WINDOW_SECONDS:
            return False
        if len(self._seen) >= self.MAX_TRACKED:
            # Drop everything already outside its window; if that frees
            # nothing, start over rather than grow. Losing throttle state means
            # at worst one extra mail, which is the safe direction here.
            self._seen = {k: t for k, t in self._seen.items() if now - t < self.WINDOW_SECONDS}
            if len(self._seen) >= self.MAX_TRACKED:
                self._seen.clear()
        self._seen[key] = now
        return True


__all__ = ["ErrorAlertThrottleFilter", "rate_limit"]
