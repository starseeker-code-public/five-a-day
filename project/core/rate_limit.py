"""
Simple session + cache rate limiter (v1.10).

Used to throttle login attempts (5/min per IP) and the parent-portal magic
link request. Django's local-memory cache is the default, so this works on a
single-instance deployment out of the box; swap to Redis via CACHES for Cloud
Run's multi-instance setup without any code change.
"""

from __future__ import annotations

import logging
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse

from core.log_safe import safe_log

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_SECONDS = 60
DEFAULT_LIMIT = 5


def _client_ip(request) -> str:
    fwd = request.META.get("HTTP_X_FORWARDED_FOR")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _cache_key(scope: str, ip: str) -> str:
    return f"rl:{scope}:{ip}"


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

            key = _cache_key(scope, _client_ip(request))
            # `add()` is atomic in memcached and Redis: it seeds the counter to
            # 0 with the correct TTL only when the key is missing. This closes
            # the TOCTOU race where two concurrent requests both `get() == 0`
            # and then race on `set()`, resetting the window and allowing
            # >limit requests through.
            cache.add(key, 0, timeout=window_seconds)
            try:
                current = cache.incr(key)
            except ValueError:
                # Key expired between `add` and `incr` — reseed and count 1.
                cache.set(key, 1, timeout=window_seconds)
                current = 1

            if current > limit:
                logger.info(
                    "rate limit exceeded: scope=%s ip=%s current=%s",
                    scope,
                    safe_log(_client_ip(request)),
                    current,
                )
                return HttpResponse(
                    "⚠️ Demasiados intentos. Prueba de nuevo en un minuto.",
                    status=429,
                    content_type="text/plain; charset=utf-8",
                )
            return view_func(request, *args, **kwargs)

        return _wrapper

    return decorator


__all__ = ["rate_limit"]
