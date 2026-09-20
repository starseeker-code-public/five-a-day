"""Contact relay for the owner's personal portfolio site.

`joaquin-hm.com` is a static Vite build on Netlify with no backend of its own.
Its "Contact" section needs to deliver a message to a human inbox, and this
service is already up, already holds a working SMTP account and already renders
HTML mail — so the portfolio posts here rather than growing a second deployment
or paying for Netlify Forms.

That makes this module a SECOND TENANT of the academy's deployment, and it is
written to stay one. Nothing here imports an academy model, the email template
carries none of the academy's branding or its data-protection notice, and the
two settings it reads (`PORTFOLIO_CONTACT_TOKEN`, `PORTFOLIO_CONTACT_RECIPIENT`)
are read nowhere else. Deleting this file, its template, its two settings and
its URL removes the feature completely.

WHO IS ALLOWED TO CALL IT
    A bearer token, held by a Netlify Function — never by a browser. The
    function is the only caller: it keeps the secret server-side and posts the
    visitor's message on their behalf. Putting the token in the React bundle
    instead would publish it to everyone who opens devtools, and a leaked token
    on an endpoint that mails arbitrary text is a spam relay with this domain's
    reputation behind it.

WHAT A LEAKED TOKEN WOULD BUY
    Deliberately as little as possible. The recipient is FIXED by settings and
    can never be chosen by the caller, the field list is fixed here, and lengths
    are capped — so the worst case is unsolicited mail to the owner's own inbox
    at the rate limit, not mail to third parties from this domain.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from comms.services.email_service import email_service
from core.log_safe import safe_log
from core.rate_limit import rate_limit

logger = logging.getLogger(__name__)

#: The fields the relay accepts, as (key, label for the email, required?).
#: Fixed HERE rather than taken from the request, for the same reason
#: `CONTACT_FIELDS` and `SUPPORT_CATEGORIES` are: a caller who chooses their own
#: field LABELS decides what the reader believes the message says. Anything not
#: listed is dropped rather than rendered.
PORTFOLIO_FIELDS = (
    ("name", "Name", True),
    ("email", "Email", True),
    ("company", "Company", False),
    ("subject", "Subject", False),
    ("message", "Message", True),
)

#: Long enough for a real enquiry, short enough that one POST cannot fill an
#: inbox. Values are TRUNCATED rather than refused — someone who pastes a job
#: description should still be heard from. `message` gets the larger share
#: because it is the only field with anything to say.
MAX_FIELD_LENGTH = 500
MAX_MESSAGE_LENGTH = 5000

#: Rate limiting here is a GLOBAL cap, not a per-visitor one, and the number
#: reflects that: every legitimate request arrives from the Netlify Function's
#: egress address, so `rate_limit`'s per-IP bucket holds the whole site's
#: traffic in a single slot set. Per-VISITOR throttling belongs in the function,
#: which is the only layer that can see who the visitor actually is.
#:
#: So this limit answers a different question — "how much mail can a stolen
#: token produce before someone notices" — and 20 per 10 minutes is chosen to be
#: far above a portfolio's real traffic and far below inbox-flooding.
RATE_LIMIT = 20
RATE_WINDOW_SECONDS = 600


def _unauthorised() -> JsonResponse:
    """One response object for every rejected credential.

    Missing header, malformed header and wrong token deliberately produce the
    SAME body and status. Telling a caller which of the three it was is free
    reconnaissance — it turns "guess the token" into "confirm the header shape
    first, then guess the token".
    """
    return JsonResponse({"success": False, "error": "Unauthorised."}, status=401)


def _authorised(request) -> bool:
    """True when the request carries the configured bearer token.

    `constant_time_compare` rather than `==`: the comparison is against a secret
    and a short-circuiting compare leaks its prefix through timing, which is the
    same reason `health_check` uses it for X-Probe-Token.
    """
    expected = getattr(settings, "PORTFOLIO_CONTACT_TOKEN", "")
    if not expected:
        return False

    header = request.headers.get("Authorization", "")
    scheme, _, supplied = header.partition(" ")
    if scheme.lower() != "bearer":
        return False

    return constant_time_compare(supplied.strip(), expected)


@csrf_exempt
@require_http_methods(["POST"])
@rate_limit("portfolio_contact", limit=RATE_LIMIT, window_seconds=RATE_WINDOW_SECONDS)
def submit_portfolio_contact(request):
    """Relay one message from the portfolio's contact form to the owner.

    `csrf_exempt` is the third in the app, and it carries the same
    justification as the other two: the caller is a SERVER, authenticated by
    something other than a session. Stripe's webhook proves itself with a
    signature, this one with a bearer token. There is no session and no cookie
    to protect here — CSRF defends a browser's ambient authority, and a caller
    holding a secret header has none.

    The decorator ORDER matters and is not arbitrary:

    * `csrf_exempt` outermost, so the attribute is on the object
      `CsrfViewMiddleware` actually inspects;
    * `rate_limit` INSIDE the method check but OUTSIDE the token check, so a
      caller guessing tokens is throttled too. Authenticating first would leave
      the guesses unmetered, which is the half that needs metering most.
    """
    # Configuration is checked BEFORE the token: with no secret configured there
    # is nothing to compare against, and `_authorised` already refuses in that
    # case. Answering 503 here says "this deployment does not offer the relay",
    # which is the truth, instead of a 401 that reads as "wrong token" and sends
    # the next hour into debugging a credential that was never the problem.
    token = getattr(settings, "PORTFOLIO_CONTACT_TOKEN", "")
    recipient = getattr(settings, "PORTFOLIO_CONTACT_RECIPIENT", "")
    if not token or not recipient:
        missing = [
            name
            for name, value in (
                ("PORTFOLIO_CONTACT_TOKEN", token),
                ("PORTFOLIO_CONTACT_RECIPIENT", recipient),
            )
            if not value
        ]
        logger.error("Portfolio contact relay is not configured — missing %s", ", ".join(missing))
        return JsonResponse({"success": False, "error": "Contact relay is not configured."}, status=503)

    if not _authorised(request):
        # WARNING, not INFO: nothing but a misconfigured deploy or somebody
        # probing should ever reach this line, and both are worth seeing.
        logger.warning("Rejected an unauthorised portfolio contact attempt")
        return _unauthorised()

    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"success": False, "error": "Body must be JSON."}, status=400)
    if not isinstance(payload, dict):
        # `json.loads("[]")` and `json.loads("2")` both parse fine and then fail
        # on `.get` with a 500. A list is a client bug, not a server fault.
        return JsonResponse({"success": False, "error": "Body must be a JSON object."}, status=400)

    values: dict[str, str] = {}
    missing_fields: list[str] = []
    for key, label, required in PORTFOLIO_FIELDS:
        cap = MAX_MESSAGE_LENGTH if key == "message" else MAX_FIELD_LENGTH
        raw = payload.get(key, "")
        # `str()` rather than a type check: a caller sending `{"name": 42}` means
        # "42", and refusing it would be pedantry. `None` is the one value that
        # must not become the string "None" in somebody's inbox.
        value = "" if raw is None else str(raw).strip()[:cap]
        if required and not value:
            missing_fields.append(label)
        values[key] = value

    if missing_fields:
        return JsonResponse(
            {"success": False, "error": f"Missing required fields: {', '.join(missing_fields)}."},
            status=400,
        )

    subject_line = values["subject"] or "New message from the portfolio"
    sent = email_service.send_email(
        template_name="portfolio_contact",
        recipients=recipient,
        subject=f"[Portfolio] {subject_line} — {values['name']}",
        context={
            # Pairs, not a dict, so the email renders the fields in the order
            # they are declared above and never shows an empty row.
            #
            # `message` is EXCLUDED: the template gives it its own quoted block
            # above the table. Leaving it in the rows too renders it twice —
            # which is not just untidy, it doubles the one field that carries
            # the 5 000-character cap, so a long enquiry produced an 18 KB
            # message body with the same wall of text in it twice.
            "rows": [(label, values[key]) for key, label, _ in PORTFOLIO_FIELDS if values[key] and key != "message"],
            "sender_name": values["name"],
            "reply_to": values["email"],
            "message": values["message"],
        },
        # The SMTP account belongs to the academy, so `From:` is its address and
        # cannot be anything else. Without this header, hitting Reply in the
        # owner's client answers the academy's own Gmail rather than the person
        # who wrote in — the template's mailto button exists for the same reason
        # and stays as the fallback for clients that ignore Reply-To.
        reply_to=[values["email"]],
    )
    if not sent:
        # send_email has already logged the cause with a traceback.
        return JsonResponse({"success": False, "error": "The message could not be sent."}, status=502)

    logger.info("Portfolio contact relayed from %s", safe_log(values["email"]))
    return JsonResponse({"success": True})


__all__ = ["PORTFOLIO_FIELDS", "submit_portfolio_contact"]
