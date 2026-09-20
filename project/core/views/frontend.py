"""The public React site — served by Django at the origin root.

`frontend/` holds a Vite + React single-page app (the academy's public-facing
site). It used to deploy to Netlify on its own domain; it is now built into
`frontend/dist/` and served from here, so one origin carries both halves: the
marketing site at "/" and the management app under `settings.APP_URL_PREFIX`.

HOW THE TWO PIECES ARE SERVED
    index.html   this module, for "/" and for every route in SPA_ROUTES —
                 the per-route file from `frontend/scripts/generate-seo.mjs`
                 when it exists, otherwise the shared shell.
    everything   WhiteNoise, straight from `settings.WHITENOISE_ROOT`
    else         (= frontend/dist), so /assets/…, /images/… and /videos/…
                 resolve at the same absolute paths the sources already use.

WHY DJANGO SERVES THE HTML RATHER THAN WHITENOISE
    `NoHtmlCacheMiddleware` marks it `no-cache`. Vite content-hashes the
    bundle, so a browser-cached index.html pins asset hashes from the previous
    deploy — the exact staleness the middleware was written for. Letting
    WhiteNoise serve it as an index file would skip that middleware and
    reintroduce it for the public site only.
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from comms.services.email_service import email_service
from core.rate_limit import rate_limit

logger = logging.getLogger(__name__)

#: The public routes React renders, mirroring `<Route path=…>` in
#: frontend/src/App.jsx. They are ENUMERATED rather than served by a catch-all
#: so an unknown path is still an honest 404: React Router has no catch-all
#: route, so a catch-all here would answer every typo with a 200 and a blank
#: page. `tests/integration/test_frontend_site.py` parses App.jsx and fails if
#: the two lists drift.
SPA_ROUTES = (
    "quienes-somos",
    "nuestra-metodologia",
    "ods",
    "sobre-la-academia",
    "faq",
    "aviso-legal",
)


def _index_path(route: str = "") -> Path:
    """The built HTML for one route, or the shared shell when `route` is empty.

    `frontend/scripts/generate-seo.mjs` writes one real file per page —
    `dist/faq/index.html` and so on — each carrying its own `<title>`,
    description, canonical URL and JSON-LD. Before that every route was served
    the identical document, so Google saw seven pages with one title between
    them and could rank none of them for its own subject.

    On Netlify the static server picked those files up for free. Django has to
    go looking, which is why this function exists and why the lookup is by
    ROUTE NAME rather than by `request.path`: the caller has already matched a
    URL pattern, so the value is one of `SPA_ROUTES` and never user input. A
    path joined from the request would be a directory-traversal question; this
    is not one.
    """
    base = Path(settings.FRONTEND_DIST_DIR)
    return base / route / "index.html" if route else base / "index.html"


@require_http_methods(["GET", "HEAD"])
@ensure_csrf_cookie
def frontend_index(request, route: str = ""):
    """Return the built HTML for "/" and every route in SPA_ROUTES.

    `route` is supplied by the URL conf as a static extra kwarg, never parsed
    from the request — see `_index_path`.

    Each route is served its OWN built document when one exists, carrying that
    page's title, description, canonical URL and structured data; React Router
    then reads the path from the URL bar and renders the matching page. Before
    the SEO generator these were all the identical file, which is what made
    every page of the site compete for one title in Google.

    `ensure_csrf_cookie` is what lets the contact form POST at all. This view
    returns a FILE, not a rendered template, so nothing here would otherwise
    emit a `csrftoken` cookie and every submission would be refused by
    `CsrfViewMiddleware` with a 403. The alternative — exempting the contact
    endpoint — would make it the third `@csrf_exempt` view in the app, and the
    two that exist are each authenticated by something else (a Stripe
    signature; nothing, for the health probe). This one is a plain public POST.

    A missing build is a 404 with an explanation rather than a 500: in
    development the tree is only there once somebody has run `make
    frontend-build`, and a stack trace would suggest a code fault rather than a
    missing step. In production the image cannot be built without it, so this
    branch means the Docker build skipped the node stage — worth an ERROR.
    """
    # The per-route file when the SEO generator produced one, else the shared
    # shell. The FALLBACK is not a nicety: `frontend/dist` is gitignored, the
    # test suite stubs a bare `index.html` (conftest's `spa_shell`), and a
    # developer who has run `vite build` without the generator has no per-route
    # files either. In all three cases the site must still serve every route —
    # just with the homepage's metadata, which costs SEO and breaks nothing.
    index = _index_path(route)
    if route and not index.is_file():
        index = _index_path()

    try:
        html = index.read_text(encoding="utf-8")
    except OSError:
        if settings.DEBUG:
            logger.warning("Frontend build missing at %s — run `make frontend-build`.", index)
            raise Http404(
                "El frontend no está compilado todavía. Ejecuta `make frontend-build` (o `npm run build`) y recarga."
            ) from None
        logger.error("Frontend build missing at %s in a non-DEBUG environment.", index)
        raise Http404("Frontend no disponible.") from None

    # Content-Type set explicitly: this is a file read, not a template render,
    # so nothing else would label it — and NoHtmlCacheMiddleware keys its
    # no-cache header on text/html.
    return HttpResponse(html, content_type="text/html; charset=utf-8")


# ---------------------------------------------------------------------------
# Contact form
# ---------------------------------------------------------------------------

#: The fields the form posts, mirroring `contactForm.fields` in
#: frontend/src/data.js: (form name, label for the email, required?).
#: Server-side rather than taken from the request, for the same reason
#: `SUPPORT_CATEGORIES` is: a submitter who chooses their own field LABELS
#: decides what the reader believes the message says. Anything not listed here
#: is dropped rather than rendered.
CONTACT_FIELDS = (
    ("nombre", "Nombre", True),
    ("apellidos", "Apellidos", False),
    ("email", "Email", True),
    ("telefono", "Teléfono", True),
    ("horario", "Horario preferente", False),
    ("edad", "Edad del estudiante", False),
    ("mensaje", "Mensaje", True),
)

#: Netlify's honeypot convention, kept because the markup already carries it:
#: a hidden input no human fills in. A non-empty value means a bot, and the
#: answer is a cheerful 200 with nothing sent — telling a spammer it was
#: rejected just teaches them to stop filling it in.
HONEYPOT_FIELD = "bot-field"

#: Generous enough that a family retyping a long message is never blocked,
#: tight enough that the academy's inbox cannot be flooded from one address.
MAX_FIELD_LENGTH = 2000


@require_http_methods(["POST"])
@rate_limit("public_contact_form", limit=5, window_seconds=600)
def submit_contact_form(request):
    """Email the academy a message from the public site's contact form.

    This replaces Netlify Forms, which is what handled the form while the site
    was deployed there and which stops existing the moment Django serves it.
    The failure that mattered: the React handler shows "mensaje enviado" on any
    response it manages to receive, so a form posting into a void looked like a
    working form to the family and delivered nothing to the academy. The
    endpoint therefore answers a real `{"success": …}` and the caller checks it.

    Public and unauthenticated by necessity — prospective families have no
    account. That is why it is rate-limited, length-capped and honeypotted, and
    why every value is escaped by the template rather than trusted.
    """
    if request.POST.get(HONEYPOT_FIELD, "").strip():
        logger.info("Contact form honeypot tripped; discarding silently")
        return JsonResponse({"success": True})

    values = {}
    missing = []
    for name, label, required in CONTACT_FIELDS:
        value = request.POST.get(name, "").strip()[:MAX_FIELD_LENGTH]
        if required and not value:
            missing.append(label)
        values[name] = value

    if missing:
        return JsonResponse(
            {"success": False, "error": f"Faltan campos obligatorios: {', '.join(missing)}."},
            status=400,
        )

    recipient = getattr(settings, "CONTACT_FORM_RECIPIENT", None)
    if not recipient:
        # Configuration, not user error. Say so in the log and give the family
        # a message that points them at a channel that does work, rather than
        # a bare failure they can only read as "my message vanished".
        logger.error("CONTACT_FORM_RECIPIENT is not set — the contact form cannot deliver.")
        return JsonResponse(
            {"success": False, "error": "No hemos podido enviar el mensaje. Escríbenos por WhatsApp."},
            status=503,
        )

    full_name = " ".join(p for p in (values["nombre"], values["apellidos"]) if p)
    sent = email_service.send_email(
        template_name="contact_form",
        recipients=recipient,
        subject=f"Nuevo contacto desde la web — {full_name}",
        context={
            # Pairs, not a dict, so the email renders the fields in the order
            # the form asks them and never shows an empty row.
            "rows": [(label, values[name]) for name, label, _ in CONTACT_FIELDS if values[name]],
            "full_name": full_name,
            "reply_to": values["email"],
            "message": values["mensaje"],
        },
    )
    if not sent:
        # send_email already logged the cause. The family gets a fallback that
        # reaches a human, which is the whole point of the form.
        return JsonResponse(
            {"success": False, "error": "No hemos podido enviar el mensaje. Escríbenos por WhatsApp."},
            status=502,
        )

    logger.info("Contact form delivered to the academy")
    return JsonResponse({"success": True})


__all__ = ["CONTACT_FIELDS", "SPA_ROUTES", "frontend_index", "submit_contact_form"]
