"""Validation policy for email addresses typed by the PUBLIC into a form.

This is a leaf module: stdlib plus `django.core.validators`, no models, no
settings, no views. It is imported by `core.views.frontend` (the academy's
contact form) and is deliberately reusable by any other public form that grows
one — the portfolio relay included.

It answers with an error CODE rather than a sentence, because the two callers
that need it speak different languages to their visitors (the academy's site is
Spanish, the portfolio's is English). The wording belongs to the view; the rule
belongs here, in one place, with the tests.

WHY AN ALLOWLIST OF DOMAINS, AND WHAT IT COSTS
    Syntax validation cannot tell a real mailbox from a throwaway one, and a
    public form that emails the academy on demand is worth abusing. Restricting
    the domain to the mailbox providers real families actually use removes the
    entire disposable-address industry in one rule.

    The cost is real and must not be hidden: a family whose only address is at
    their employer, their own domain or a small local ISP is REFUSED. That is
    why the view's message names WhatsApp and the academy's own address — the
    enquiry has to survive the rejection — and why a refusal logs the DOMAIN
    (never the address; this endpoint is public). Those log lines are the
    evidence for what to add here: if the academy is turning away real people,
    it will be visible and fixable rather than silent.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator

#: Mailbox providers a prospective family in Albacete plausibly uses: the global
#: consumer services, their Spanish regional spellings, and the Spanish ISPs
#: that still carry a lot of older addresses. Ordered by family, not by rank, so
#: an addition lands somewhere obvious.
#:
#: Add a domain HERE when the refusal log shows real people being turned away.
#: It is a plain frozenset rather than a setting because a value the academy can
#: change at 2am without review is how an allowlist quietly becomes "*".
ALLOWED_EMAIL_DOMAINS = frozenset(
    {
        # Google
        "gmail.com",
        "googlemail.com",
        # Microsoft
        "outlook.com",
        "outlook.es",
        "hotmail.com",
        "hotmail.es",
        "live.com",
        "live.es",
        "msn.com",
        # Apple
        "icloud.com",
        "me.com",
        "mac.com",
        # Yahoo
        "yahoo.com",
        "yahoo.es",
        "ymail.com",
        "rocketmail.com",
        # Other global consumer providers
        "aol.com",
        "gmx.com",
        "gmx.es",
        "mail.com",
        "zoho.com",
        "yandex.com",
        "fastmail.com",
        # Privacy-focused providers, in wide ordinary use
        "protonmail.com",
        "protonmail.ch",
        "proton.me",
        "pm.me",
        "tutanota.com",
        "tuta.com",
        # Spanish ISPs — a lot of long-standing family addresses live here
        "telefonica.net",
        "movistar.es",
        "terra.es",
        "ono.com",
        "orange.es",
        "wanadoo.es",
        "vodafone.es",
        "jazztel.es",
        "euskaltel.net",
        "yacom.es",
        "hotmail.co.uk",
        "outlook.fr",
    }
)

#: RFC 5321 §4.5.3.1: 64 octets for the local part, 255 for the whole path. A
#: value longer than this is never a real mailbox, and capping it here keeps an
#: absurd string out of the subject line and the logs.
MAX_EMAIL_LENGTH = 254
MAX_LOCAL_PART_LENGTH = 64
MAX_DOMAIN_LENGTH = 253

#: Error codes. The caller maps these to its own wording.
ERROR_REQUIRED = "required"
ERROR_SYNTAX = "syntax"
ERROR_TOO_LONG = "too_long"
ERROR_DOMAIN_NOT_ALLOWED = "domain_not_allowed"

#: Deliberately stricter than `EmailValidator`, which accepts several forms that
#: are legal in the RFC and never typed by a real person — and each of which is
#: a parsing hazard for something downstream. A quoted local part (`"a b"@x.es`)
#: can contain an `@` or a comma; an address literal (`a@[192.168.0.1]`) has no
#: domain to check at all. Refusing them narrows what the rest of the app can
#: ever be handed.
_LOCAL_PART_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
_DOMAIN_RE = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}$")

_django_validator = EmailValidator()


def check_email(raw: str) -> tuple[str, str | None]:
    """Validate and normalise one submitted address.

    Returns `(normalised, None)` when it passes, or `("", code)` when it does
    not. Normalisation lowercases the DOMAIN only: the local part is
    case-sensitive per RFC 5321 and, while every provider here treats it
    case-insensitively, rewriting somebody's address is not this function's job.
    """
    candidate = (raw or "").strip()
    if not candidate:
        return "", ERROR_REQUIRED

    # Length first: everything below scans the string, and the cap is what makes
    # that bounded regardless of what was posted.
    if len(candidate) > MAX_EMAIL_LENGTH:
        return "", ERROR_TOO_LONG

    # Exactly one "@", and no whitespace anywhere. `EmailValidator` would accept
    # a quoted local part containing either.
    if candidate.count("@") != 1 or any(ch.isspace() for ch in candidate):
        return "", ERROR_SYNTAX

    local, _, domain = candidate.partition("@")
    domain = domain.lower()

    if len(local) > MAX_LOCAL_PART_LENGTH or len(domain) > MAX_DOMAIN_LENGTH:
        return "", ERROR_TOO_LONG

    # ASCII only. An SMTPUTF8 address is legal and this academy's Gmail account
    # cannot be relied on to route one; accepting it would mean taking an
    # enquiry we then fail to answer, which is worse than refusing it plainly.
    if not candidate.isascii():
        return "", ERROR_SYNTAX

    if not _LOCAL_PART_RE.match(local) or not _DOMAIN_RE.match(domain):
        return "", ERROR_SYNTAX

    # Django's validator last, as a belt-and-braces cross-check rather than the
    # primary rule: the patterns above are stricter, so reaching this line and
    # failing here means they disagree and the address is refused either way.
    try:
        _django_validator(f"{local}@{domain}")
    except ValidationError:
        return "", ERROR_SYNTAX

    if domain not in ALLOWED_EMAIL_DOMAINS:
        return "", ERROR_DOMAIN_NOT_ALLOWED

    return f"{local}@{domain}", None


def domain_of(raw: str) -> str:
    """The domain part, for logging a refusal without logging the person.

    This endpoint is public and unauthenticated, so the address itself is
    somebody's personal data arriving from a stranger; the domain is what tells
    the academy whether the allowlist is turning real families away.
    """
    head, at, domain = (raw or "").strip().rpartition("@")
    # `rpartition` returns the WHOLE string as its third element when the
    # separator is absent, so without this guard a value with no "@" in it —
    # somebody's name, a typo, anything — was logged verbatim. That is the one
    # thing this function exists to prevent.
    if not at or not head:
        return ""
    return domain.lower()[:MAX_DOMAIN_LENGTH]


__all__ = [
    "ALLOWED_EMAIL_DOMAINS",
    "ERROR_DOMAIN_NOT_ALLOWED",
    "ERROR_REQUIRED",
    "ERROR_SYNTAX",
    "ERROR_TOO_LONG",
    "MAX_EMAIL_LENGTH",
    "check_email",
    "domain_of",
]
