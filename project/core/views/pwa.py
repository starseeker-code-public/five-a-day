"""PWA endpoints — web manifest + service worker (v1.12)."""

from __future__ import annotations

from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_http_methods


@require_http_methods(["GET"])
@cache_control(public=True, max_age=3600)
def web_manifest(request):
    """
    Web app manifest (https://developer.mozilla.org/en-US/docs/Web/Manifest).
    Serves as JSON so the browser can install the app on the home screen.
    """
    manifest = {
        "name": "Five a Day",
        "short_name": "Five a Day",
        "description": "Gestión de estudiantes para la academia Five a Day.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#f8fafc",
        "theme_color": "#6d28d9",
        "lang": "es",
        "icons": [
            {
                "src": "/static/images/logo_white_bg.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable",
            },
            {
                "src": "/static/images/logo_white_bg.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable",
            },
        ],
        "shortcuts": [
            {"name": "Panel", "url": "/", "short_name": "Panel"},
            {"name": "Estudiantes", "url": "/students/", "short_name": "Alumnos"},
            {"name": "Pagos", "url": "/payments/", "short_name": "Pagos"},
        ],
    }
    return JsonResponse(manifest)


# The service worker itself is a small, static-ish JS file we ship inline
# rather than routing through the static-files pipeline. Keeping it here
# means it's always at /sw.js (a fixed origin path — service workers can't
# be served from arbitrary paths without extra headers) and can reference
# APP_VERSION for cache-busting.
_SW_TEMPLATE = """// Five a Day — service worker (v1.12)
// Cache-first for GETs to same-origin URLs so the dashboard shell + static
// assets stay responsive over flaky connections; network-first everywhere
// else so the app never serves stale data by mistake.

const CACHE_NAME = "fiveaday-v%(cache_key)s";
const SHELL_URLS = [
    "/",
    "/students/",
    "/payments/",
    "/static/images/logo_white_bg.png",
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS)).catch(() => null)
    );
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((names) =>
            Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
        )
    );
    self.clients.claim();
});

self.addEventListener("fetch", (event) => {
    const req = event.request;
    if (req.method !== "GET" || new URL(req.url).origin !== self.location.origin) {
        return;
    }
    // Skip auth / API endpoints — we never want a stale login page or payments list
    const path = new URL(req.url).pathname;
    if (path.startsWith("/api/") || path.startsWith("/login/") || path.startsWith("/logout/")) {
        return;
    }
    event.respondWith(
        caches.match(req).then((cached) => {
            const network = fetch(req).then((res) => {
                if (res && res.ok) {
                    const clone = res.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(req, clone));
                }
                return res;
            }).catch(() => cached);
            return cached || network;
        })
    );
});
"""


@require_http_methods(["GET"])
def service_worker(request):
    """Serve /sw.js. Cached client-side for 1 hour; the version key inside the
    file itself invalidates the client cache on each deploy."""
    from django.conf import settings

    version = getattr(settings, "APP_VERSION", "1.0")
    body = _SW_TEMPLATE % {"cache_key": version}
    response = HttpResponse(body, content_type="application/javascript")
    response["Cache-Control"] = "public, max-age=3600"
    # Service workers must be served with a "Service-Worker-Allowed: /" header
    # if you want them to control the whole origin. Ours is at /sw.js which
    # implicitly scopes to /, so this header is really about future-proofing
    # if we ever move the file.
    response["Service-Worker-Allowed"] = "/"
    return response


__all__ = ["service_worker", "web_manifest"]
