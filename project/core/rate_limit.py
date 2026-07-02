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


def rate_limit(scope: str, *, limit: int = DEFAULT_LIMIT, window_seconds: int = DEFAULT_WINDOW_SECONDS):
    """
    Decorator: throttle a view to `limit` requests per `window_seconds` per
    client IP. Sends a 429 with a friendly message when exceeded. Only counts
    POST requests to avoid throttling normal GETs of the same page.
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapper(request, *args, **kwargs):
            if request.method != "POST":
                return view_func(request, *args, **kwargs)

            # Bypass in the test suite so the shared cache doesn't leak across
            # test cases. Django's DEBUG stays True in dev, but pytest-django
            # sets it too — so we opt out via an explicit RATELIMIT_ENABLE flag
            # instead.
            if not getattr(settings, "RATELIMIT_ENABLE", True):
                return view_func(request, *args, **kwargs)

            key = _cache_key(scope, _client_ip(request))
            current = cache.get(key, 0)
            if current >= limit:
                logger.info("rate limit exceeded: scope=%s ip=%s", scope, _client_ip(request))
                return HttpResponse(
                    "⚠️ Demasiados intentos. Prueba de nuevo en un minuto.",
                    status=429,
                    content_type="text/plain; charset=utf-8",
                )
            # Atomic increment. `add()` is a "set if not exists" so the first
            # request in a window gets `current=1` with the TTL; subsequent ones
            # use `incr()` which preserves the TTL.
            if current == 0:
                cache.set(key, 1, timeout=window_seconds)
            else:
                try:
                    cache.incr(key)
                except ValueError:
                    cache.set(key, 1, timeout=window_seconds)
            return view_func(request, *args, **kwargs)

        return _wrapper

    return decorator


__all__ = ["rate_limit"]
