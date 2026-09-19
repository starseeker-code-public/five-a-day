"""PWA endpoints — web manifest + service worker (v1.12)."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
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
        # The app is mounted under settings.APP_PATH_PREFIX and "/" now belongs
        # to the public React site, so an installed PWA must open — and stay
        # inside — the app. A scope of "/" would make the installed window
        # swallow the marketing site too.
        "start_url": f"{settings.APP_PATH_PREFIX}/",
        "scope": f"{settings.APP_PATH_PREFIX}/",
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
            {"name": "Panel", "url": f"{settings.APP_PATH_PREFIX}/", "short_name": "Panel"},
            {"name": "Estudiantes", "url": f"{settings.APP_PATH_PREFIX}/students/", "short_name": "Alumnos"},
            {"name": "Pagos", "url": f"{settings.APP_PATH_PREFIX}/payments/", "short_name": "Pagos"},
        ],
    }
    return JsonResponse(manifest)


# The service worker itself is a small, static-ish JS file we ship inline
# rather than routing through the static-files pipeline. Keeping it here means
# it sits at the app mount point's own root (<prefix>/sw.js), which is what
# gives it the whole app as its default scope — a worker served from
# /static/js/ could only ever control /static/ — and it can reference
# APP_VERSION for cache-busting.
_SW_TEMPLATE = """// Five a Day — service worker (v1.12)
//
// Cache strategy — deliberately narrow:
//   - Cache-first ONLY for /static/ assets (content-hashed, session-free).
//   - Network-first with cache fallback for the manifest + logo so an
//     offline load still renders the shell.
//   - Everything else (dashboard, students, payments, parent portal,
//     API): NEVER cached — those responses are user-scoped and caching
//     them would leak session data on shared devices after logout.

const CACHE_NAME = "fiveaday-v%(cache_key)s";
// DEBUG on the server. Cache-first is only safe because production serves
// content-hashed filenames, so an edited asset arrives under a NEW url that
// no cache entry can shadow. Development serves the bare path
// (/static/js/management.js), and CACHE_NAME is keyed on APP_VERSION — so
// between two releases every edited JS/CSS file is served from the cache
// FOREVER. The symptom is brutal to diagnose: the HTML is `no-cache`
// (NoHtmlCacheMiddleware) so template changes appear instantly, while the
// matching script stays stale — a new button renders and clicking it does
// nothing, and the server is serving the right file the whole time.
const DEV = %(dev)s;
const MANIFEST_URL = "%(manifest_url)s";
const STATIC_SHELL = [
    "/static/images/logo_white_bg.png",
    MANIFEST_URL,
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_SHELL)).catch(() => null)
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

function isCacheable(url) {
    const path = url.pathname;
    // Static assets never carry a session — safe to cache. In development the
    // paths are not content-hashed, so the SW is bypassed for them entirely
    // (see DEV above); production keeps cache-first, which is what hashing
    // makes optimal.
    if (path.startsWith("/static/") || path.startsWith("/media/")) return !DEV;
    // Manifest is public and identical for every user.
    if (path === MANIFEST_URL) return true;
    // NOTE: /login/ is deliberately NOT cached. It looks public, but it embeds
    // a CSRF token, and Django rotates the CSRF secret on login. Serving the
    // page cache-first handed back a token minted against an old secret, so the
    // next sign-in failed with a 403. The Cache API ignores Cache-Control, so
    // the server's `no-store` header could not prevent this on its own.
    return false;
}

self.addEventListener("fetch", (event) => {
    const req = event.request;
    if (req.method !== "GET") return;
    const url = new URL(req.url);
    if (url.origin !== self.location.origin) return;

    // Bypass the SW entirely for anything session-scoped so a logged-out
    // user cannot see the previous user's dashboard by pulling from cache.
    if (!isCacheable(url)) return;

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
    """Serve <prefix>/sw.js. Cached client-side for 1 hour; the version key
    inside the file itself invalidates the client cache on each deploy."""

    version = getattr(settings, "APP_VERSION", "1.0")
    body = _SW_TEMPLATE % {
        "cache_key": version,
        "dev": "true" if settings.DEBUG else "false",
        "manifest_url": reverse("web_manifest"),
    }
    response = HttpResponse(body, content_type="application/javascript")
    response["Cache-Control"] = "public, max-age=3600"
    # A service worker's default scope is the directory it is served from, so
    # this file — now at <prefix>/sw.js — already scopes to the app and the
    # header merely states it. It is deliberately NOT "/" any more: the origin
    # root belongs to the public React site, and a worker claiming the whole
    # origin would intercept its requests too.
    response["Service-Worker-Allowed"] = f"{settings.APP_PATH_PREFIX}/"
    return response


__all__ = ["service_worker", "web_manifest"]
