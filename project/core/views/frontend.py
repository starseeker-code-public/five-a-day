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
from django.middleware.csrf import get_token
from django.utils.html import escape
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from comms.services.email_service import email_service
from core.email_policy import ERROR_DOMAIN_NOT_ALLOWED, ERROR_TOO_LONG, check_email, domain_of
from core.rate_limit import begin_cooldown, cooldown_active, rate_limit

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


#: Where the CSRF token is published to the React bundle. The site is a BUILT
#: file, so it has no `{% csrf_token %}` input; this meta tag is its
#: equivalent, and `ContactSection.jsx` reads it.
CSRF_META_NAME = "csrf-token"


def _inject_csrf_meta(html: str, token: str) -> str:
    """Publish the CSRF token into the served HTML as a `<meta>` tag.

    WHY THIS EXISTS. `CSRF_COOKIE_HTTPONLY` is True whenever `DEBUG=False`, so
    on the testing VM and in production JS cannot read `document.cookie` — the
    React form read the cookie, sent an EMPTY `X-CSRFToken`, and every
    submission was refused with a 403 whose body is HTML. The component cannot
    parse that, so it fell through to its generic "no hemos podido enviar el
    mensaje", which reads as a mail outage and sends the investigation to the
    SMTP layer. It worked in development for the one reason that makes this
    class of bug survive to production: with `DEBUG=True` the cookie is not
    HttpOnly.

    The fix keeps the app's existing rule rather than inventing a second one:
    every other page takes the token from markup Django rendered (the hidden
    input in `base.html`, read by `base.js`), never from the cookie. A built
    file has no `{% csrf_token %}` tag, so Django writes the equivalent here.

    The alternatives were worse. Turning off `CSRF_COOKIE_HTTPONLY` weakens
    every page in the app to fix one form; `@csrf_exempt` would be the fourth
    such view, and the three that exist each justify it the same way — the
    caller is a SERVER carrying its own credential — which a public browser
    POST cannot claim.

    Injection is a plain replace on `</head>`, and a document without one is
    logged rather than silently served token-less: that failure would look
    exactly like the bug this replaces.
    """
    tag = f'<meta name="{CSRF_META_NAME}" content="{escape(token)}">'
    head_close = "</head>"
    if head_close not in html:
        logger.error("No </head> in the frontend shell; the CSRF token was not published to the page.")
        return html
    return html.replace(head_close, f"{tag}{head_close}", 1)


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

    CSRF takes BOTH halves here, and each is useless without the other.
    `ensure_csrf_cookie` sets the cookie — this view returns a FILE, not a
    rendered template, so nothing else would emit one — and `_inject_csrf_meta`
    publishes the matching token into the markup, because a built file has no
    `{% csrf_token %}` input and the cookie is HttpOnly outside development.
    Shipping only the cookie is what made every contact submission 403 on the
    testing VM while working locally. Exempting the endpoint instead would make
    it the fourth `@csrf_exempt` view, and the three that exist each justify it
    the same way — the caller is a server carrying its own credential — which a
    public browser POST cannot.

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

    # `get_token` both returns the value and marks the cookie for sending, so
    # the header the form posts and the cookie it is compared against are
    # minted together. Safe to do per request: NoHtmlCacheMiddleware marks this
    # response no-cache, so no shared cache can pin one visitor's token.
    html = _inject_csrf_meta(html, get_token(request))

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

#: Seconds one client must wait between two messages. The React form counts the
#: same number down on the button, but THIS is the control — the countdown is a
#: courtesy to somebody who double-clicked, and a script never runs it at all.
CONTACT_COOLDOWN_SECONDS = 60

_COOLDOWN_SCOPE = "public_contact_form"

#: Answered as JSON, unlike the rate limiter's own text/plain 429 — so the React
#: form can show the academy's own wording instead of falling back to a generic
#: "no hemos podido enviar", which blames the send for a wait.
_COOLDOWN_MESSAGE = "Acabas de enviarnos un mensaje. Espera un momento antes de enviar otro, o escríbenos por WhatsApp."

#: What a refused address is told. Every one of these names a channel that still
#: works: a rejection must not be the end of the enquiry, because the person
#: being refused is usually a family the academy wants to hear from, not an
#: abuser. The domain message is the one that matters — an allowlist WILL
#: occasionally turn away somebody real (a work address, a small ISP), and the
#: only acceptable version of that is one they can route around in a tap.
_EMAIL_ERRORS = {
    ERROR_TOO_LONG: "Ese email es demasiado largo. Revísalo, por favor.",
    ERROR_DOMAIN_NOT_ALLOWED: (
        "No podemos aceptar mensajes de ese dominio de correo. Escríbenos desde otra dirección "
        "(Gmail, Outlook, Hotmail, iCloud…), por WhatsApp, o a hellofiveaday@gmail.com."
    ),
}
_EMAIL_ERROR_DEFAULT = "Revisa la dirección de email: no parece válida."


@require_http_methods(["POST"])
# TWO controls, answering two different questions.
#
#   rate_limit   bounds ABUSE: five POSTs per ten minutes per client, counted
#                whether or not the view accepts them, so probing the validator
#                costs the same as sending.
#   the cooldown bounds VOLUME after a message actually goes through, and is
#                started at the bottom of this view rather than by a second
#                decorator. A decorator claims its slot BEFORE the view runs,
#                so a submission the view then refuses would still spend it —
#                and the commonest refusal here is a mistyped email. The family
#                would be told to correct it and then refused for a minute when
#                they did, which reads as the site being broken by the very act
#                of fixing the mistake.
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
    # Checked before anything else: it is the cheapest possible refusal, and it
    # only ever fires for somebody who has already had a message delivered.
    if cooldown_active(_COOLDOWN_SCOPE, request):
        return JsonResponse({"success": False, "error": _COOLDOWN_MESSAGE}, status=429)

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

    # The address gets its own pass, well beyond `type="email"` in the markup —
    # which the browser enforces and a script simply does not send. It is the
    # one field the academy will REPLY to, so an address that is merely
    # well-formed is not enough: see core/email_policy.py.
    email, email_error = check_email(values["email"])
    if email_error:
        # The DOMAIN, never the address: this endpoint is public and
        # unauthenticated, so what arrives is a stranger's personal data. The
        # domain is also the only part that is actionable — it is what says
        # whether the allowlist is refusing real families and needs widening.
        logger.info("Contact form refused an email: reason=%s domain=%s", email_error, domain_of(values["email"]))
        return JsonResponse(
            {"success": False, "error": _EMAIL_ERRORS.get(email_error, _EMAIL_ERROR_DEFAULT)},
            status=400,
        )
    values["email"] = email

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
        # The SMTP account belongs to the academy, so `From:` is its own address
        # and cannot be the family's. Without this header, hitting Reply on an
        # enquiry answers hellofiveaday@gmail.com — the academy writing to
        # itself — and the prospective family never hears back. The template's
        # mailto button says the same thing and stays as the fallback for
        # clients that ignore Reply-To, but a button is not where anybody looks
        # before pressing Reply. `submit_portfolio_contact` got this in v1.30.4;
        # the academy's own form, which runs every day, did not.
        #
        # NOTE it is a header, not a recipient: `filter_allowed_recipients` does
        # not touch it, and no mail is ever sent TO the visitor.
        reply_to=[values["email"]],
    )
    if not sent:
        # send_email already logged the cause. The family gets a fallback that
        # reaches a human, which is the whole point of the form.
        return JsonResponse(
            {"success": False, "error": "No hemos podido enviar el mensaje. Escríbenos por WhatsApp."},
            status=502,
        )

    # AFTER the send, never before: see the decorator note above.
    begin_cooldown(_COOLDOWN_SCOPE, request, CONTACT_COOLDOWN_SECONDS)
    logger.info("Contact form delivered to the academy")
    return JsonResponse({"success": True})


__all__ = [
    "CONTACT_COOLDOWN_SECONDS",
    "CONTACT_FIELDS",
    "CSRF_META_NAME",
    "SPA_ROUTES",
    "frontend_index",
    "submit_contact_form",
]
