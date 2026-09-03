"""
Parent portal (v1.9, re-authenticated in v1.27).

Email + password authentication, mirroring the staff login.

A parent chooses their password IN THE APP, never by following an emailed link.
What arrives by email is a temporary password: once when their record is
created, and again on demand from "¿Has olvidado tu contraseña?". Logging in
with one grants exactly one thing — the change-password page — and setting a
real password retires it.

No link, and therefore nothing that expires. An expiring link stranded any
family who did not read their mail that day, and every unexpired one still
sitting in an inbox was a standing key to the account. The temporary password
is stored in its own column beside the real one, so asking for a reset never
invalidates a password the family is still using — which matters because the
recovery form is unauthenticated.

The password lives on `Parent` as a Django hash, NOT on an `auth.User` —
`core.views.auth._authenticate_teacher` authenticates any auth.User, so a
family holding one would hold a staff login. The portal keeps its own
`parent_id` session and never touches django.contrib.auth.

Read-only surface: dashboard, payment history, receipts, tax certificates.
"""

from __future__ import annotations

import logging
from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.password_validation import (
    get_password_validators,
    password_validators_help_texts,
    validate_password,
)
from django.core.exceptions import ValidationError
from django.db.models import Max, Min
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from billing.models import Payment
from core.rate_limit import rate_limit
from core.utils import MAX_QUERY_YEAR, MIN_QUERY_YEAR, safe_int
from students.models import PORTAL_AUTH_PASSWORD, PORTAL_AUTH_TEMPORARY, Parent

logger = logging.getLogger(__name__)

_PARENT_SESSION_KEY = "parent_id"
#: Set when the session was opened with an emailed temporary password. While it
#: is set the portal is closed except for the change-password page.
_PARENT_MUST_CHANGE_KEY = "parent_must_change_password"
#: ISO stamp of the parent's credential at session-open. A password change bumps
#: `Parent.portal_credential_changed_at`, so a session carrying an older stamp is
#: rejected — that is how a reset logs out every OTHER device.
_PARENT_CRED_STAMP_KEY = "parent_credential_stamp"
_PARENT_SESSION_MAX_AGE = 60 * 60 * 6  # 6 hours


def _credential_stamp(parent: Parent) -> str:
    return parent.portal_credential_changed_at.isoformat() if parent.portal_credential_changed_at else ""


def _start_parent_session(request, parent_id: int, *, must_change_password: bool = False) -> None:
    """
    Start a clean session on the identity change. `flush()` gives a brand-new
    session key, which does two things:
      1. Prevents session fixation — this is a login, and a pre-existing
         session id must never survive one (django.contrib.auth.login cycles
         the key for exactly this reason; this custom login has to do it too).
      2. Drops any admin state (`is_authenticated`) that happened to be in the
         same session, so a teacher opening a parent's portal doesn't end up
         holding both identities in one cookie — and so `parent_portal_logout`
         is a real logout rather than a partial one.

    `must_change_password` marks a session opened with an emailed temporary
    password; `_require_parent` then holds it at the change-password page.
    """
    request.session.flush()
    request.session[_PARENT_SESSION_KEY] = parent_id
    parent = Parent.objects.filter(id=parent_id).first()
    if parent is not None:
        request.session[_PARENT_CRED_STAMP_KEY] = _credential_stamp(parent)
    if must_change_password:
        request.session[_PARENT_MUST_CHANGE_KEY] = True
    request.session.set_expiry(_PARENT_SESSION_MAX_AGE)


def _current_parent(request) -> Parent | None:
    pid = request.session.get(_PARENT_SESSION_KEY)
    if not pid:
        return None
    parent = Parent.objects.filter(id=pid).first()
    if parent is None:
        return None
    # A password change since this session was opened invalidates it — this is
    # how a reset ends every OTHER device. The session that performed the change
    # is re-stamped there (see parent_portal_change_password), so it survives.
    if request.session.get(_PARENT_CRED_STAMP_KEY, "") != _credential_stamp(parent):
        request.session.flush()
        return None
    return parent


def _require_parent(request, *, allow_pending_change: bool = False):
    """
    Resolve the session's parent, or hand back the redirect to send instead.

    A parent who got in on a TEMPORARY password is held at the change-password
    page: the credential they used is sitting in their inbox in plaintext, so
    every other page stays closed until they replace it. `allow_pending_change`
    is for the change-password view itself, which would otherwise redirect to
    itself forever.
    """
    parent = _current_parent(request)
    if parent is None:
        return None, redirect("parent_portal_login")
    if not allow_pending_change and request.session.get(_PARENT_MUST_CHANGE_KEY):
        return None, redirect("parent_portal_change_password")
    return parent, None


def _parent_by_email(email: str) -> Parent | None:
    """
    Resolve the single `Parent` holding `email`, or None.

    `Parent.email` is NOT unique — only `dni` is — so more than one row can
    carry the same address: a couple who share a mailbox, or a duplicated
    record. `filter(...).first()` resolved that silently to the lowest pk,
    which is the worst possible answer for a login: one family would sign in
    and be shown ANOTHER family's payment history and tax certificates, while
    the second parent could never sign in at all.

    An ambiguous match is therefore refused rather than resolved arbitrarily —
    the same call `core.views.auth._authenticate_teacher` makes about
    `auth.User.email`, for the same reason. `[:2]` is enough to tell "one" from
    "more than one" without reading the whole set.

    The duplicate is logged (without the address — this is the enumeration
    boundary) so an admin can merge the rows; until they do, neither parent
    gets in, which is the safe direction to fail.
    """
    candidates = list(Parent.objects.filter(email__iexact=email)[:2])
    if len(candidates) != 1:
        if len(candidates) > 1:
            logger.error(
                "Parent portal: refusing an ambiguous email match — %d parent rows share one address",
                len(candidates),
            )
        return None
    return candidates[0]


def _parent_password_validators():
    """
    The portal's own validator set (`settings.PARENT_PASSWORD_VALIDATORS`).

    NOT `AUTH_PASSWORD_VALIDATORS`, which demands 12 characters because those
    accounts are effectively superusers over a database of minors' personal
    data. A family gets a read-only view of their own children and their own
    invoices, so the staff bar buys almost nothing there and costs onboarding —
    which at this academy means phone calls.

    Built per call rather than cached at import: `override_settings` has to be
    able to change it, and instantiating three small validator objects is not a
    cost worth a cache-invalidation bug.
    """
    return get_password_validators(settings.PARENT_PASSWORD_VALIDATORS)


def _password_rules_context(parent, **extra):
    """
    Context shared by every render of the change-password page, so the GET and
    the rejected-POST cannot drift.

    `password_help` comes from the validators rather than being typed into the
    template: a hard-coded list is wrong the moment the validator set changes,
    and a page stating the wrong rule is worse than one stating none.
    """
    return {
        "parent": parent,
        "password_help": password_validators_help_texts(_parent_password_validators()),
        **extra,
    }


# ── Temporary-password email ────────────────────────────────


def send_portal_temporary_password(request, parent, *, reset: bool = False) -> bool:
    """
    Queue an email carrying a freshly generated temporary password.

    Shared by the once-only invitation (fired when the parent record is created)
    and the self-service recovery form, because they differ only in the copy.

    The password is generated INSIDE the task, not here: the plaintext is a live
    credential, and a task argument is serialised into the broker (Redis, in
    development) and shows up in task logs. Generating it at the point of use
    keeps it in one function and out of every queue and log line.

    Returns False when the parent has no address to write to, or when the task
    could not be enqueued. Never raises — an SMTP problem must not break the
    enrolment this is a side effect of.
    """
    if not parent.email:
        return False

    from comms.tasks import send_parent_temporary_password_task

    # Only the (non-secret) login URL crosses the task boundary — see the task's
    # docstring for why the password itself does not.
    login_url = request.build_absolute_uri(reverse("parent_portal_login"))

    try:
        send_parent_temporary_password_task.delay(parent.id, login_url, reset)
    except Exception:  # noqa: BLE001 — never fail the request over email
        logger.exception("Failed to enqueue portal password email for parent %d", int(parent.id))
        return False
    return True


def send_portal_invitation_once(request, parent) -> bool:
    """
    Send the portal invitation the FIRST time only.

    A family with three children goes through the enrolment flow three times
    and must still receive exactly one invitation, so the guard is a timestamp
    on the parent rather than a count of anything. It is stamped BEFORE the
    send is queued: a duplicate invite is worse than a missed one, because the
    missed one is recoverable from "¿Has olvidado tu contraseña?" while the
    duplicate is an unexplained second email about a family's payment history.
    """
    if parent.portal_invite_sent_at is not None or not parent.email:
        return False

    parent.portal_invite_sent_at = timezone.now()
    parent.save(update_fields=["portal_invite_sent_at", "updated_at"])
    return send_portal_temporary_password(request, parent, reset=False)


# ── Login flow ────────────────────────────────────────────


@rate_limit("parent_portal_login", limit=5, window_seconds=60)
@require_http_methods(["GET", "POST"])
def parent_portal_login(request):
    """
    Email + password, the same shape as the staff login.

    Rate-limited to 5 POSTs / minute / IP — the portal shows a family's payment
    history and downloads their tax certificate, so it is worth as much to an
    attacker as the staff login is.
    """
    # Resolve the row, not just the session key: if the Parent was deleted while
    # a session lingered (admin delete, or a QA `seed_database --reset`), keying
    # on the bare id sent them to the dashboard, which `_require_parent` bounced
    # back to login, which bounced back here — an infinite redirect. A stale
    # session is cleared so this renders the login form normally.
    if _current_parent(request) is not None:
        return redirect("parent_portal_dashboard")
    request.session.pop(_PARENT_SESSION_KEY, None)

    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        password = request.POST.get("password") or ""

        parent = _parent_by_email(email)
        if parent is not None:
            matched = parent.authenticate_portal(password)
        else:
            # Spend the same hashing work a real failed login costs, so an
            # unknown address cannot be told from a known one by response time —
            # the enumeration defence the unified error message assumes.
            from students.models import burn_portal_login_work

            burn_portal_login_work(password)
            matched = None
        if matched is not None:
            # A temporary password is a credential sitting in plaintext in the
            # family's inbox, so it buys exactly one thing: the right to replace
            # it. `_require_parent` keeps the rest of the portal shut until
            # they do.
            temporary = matched == PORTAL_AUTH_TEMPORARY
            _start_parent_session(request, parent.id, must_change_password=temporary)
            if temporary:
                messages.info(request, "Por seguridad, elige una contraseña nueva para continuar.")
                return redirect("parent_portal_change_password")
            return redirect("parent_portal_dashboard")

        # One message for "no such email", "wrong password" and "never set a
        # password". The first would confirm which addresses belong to families
        # of the academy; the third would tell an attacker exactly which
        # accounts are sitting on an unclaimed invitation link.
        logger.warning("Parent portal login rejected")
        messages.error(request, "❌ Email o contraseña incorrectos")
        return render(request, "parent_portal/login.html", {"email": email})

    return render(request, "parent_portal/login.html", {})


@rate_limit("parent_portal_forgot", limit=5, window_seconds=60)
@require_http_methods(["GET", "POST"])
def parent_portal_forgot_password(request):
    """
    Self-service recovery — also the way in for a parent whose invitation was
    never opened, which is why the page never says whether a password exists.

    Emails a fresh TEMPORARY password rather than a link. It is stored beside
    the parent's real one, so requesting a reset never invalidates a password
    the family is still using: this endpoint is unauthenticated, and an
    overwrite here would let anyone who knows a family's address lock them out
    of their own payment history.

    The response is identical for a known and an unknown address. The dominant
    per-request work is a password hash; an unknown address burns an equivalent
    one so hashing time cannot separate the two. (The email send for a known
    address is still additional work; the rate limit and the identical response
    body are what bound that residual, and production has no broker to defer it
    to — do not "fix" that by branching the response.)
    """
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        if not email:
            messages.error(request, "Introduce un email válido.")
            return render(request, "parent_portal/forgot_password.html", {})

        parent = _parent_by_email(email)
        if parent is not None:
            send_portal_temporary_password(request, parent, reset=True)
        else:
            # Match the make_password() cost the known branch pays inside
            # issue_temporary_password, so an unknown address is not measurably
            # faster. Deliberately not logging the address — the log is a place
            # the "is this email registered" story leaks from.
            from students.models import burn_portal_login_work

            burn_portal_login_work(email)
            logger.info("Parent portal recovery requested for an unregistered email")

        return render(request, "parent_portal/login_sent.html", {"email": email})

    return render(request, "parent_portal/forgot_password.html", {})


@rate_limit("parent_portal_change_password", limit=5, window_seconds=300)
@require_http_methods(["GET", "POST"])
def parent_portal_change_password(request):
    """
    Set or change the portal password, in the app, on a logged-in session.

    This is the ONLY way a parent password is ever chosen. There is no
    set-password link: an emailed link that expires strands families who did
    not open their mail the same day, and every one still live in an inbox is a
    standing key to the account. What gets emailed is a temporary password
    instead, and it is spent by arriving here.

    Two modes, and the difference is what already proved identity:

    * **Forced** — the session was opened with a temporary password, so the
      family typed a credential minutes ago. Asking for it a second time is
      friction that buys nothing, and `_require_parent` has pinned them to this
      page until they finish.
    * **Voluntary** — a normal session, reached from "Cambiar contraseña". The
      CURRENT password is required, because a session cookie is not proof that
      the person at the keyboard is the account holder (a shared family laptop
      is the ordinary case here, not the exotic one).

    Rate-limited 5 per 5 minutes per IP for the same reason the staff
    `change_password` is: the voluntary mode takes the current password.
    """
    parent, redirect_resp = _require_parent(request, allow_pending_change=True)
    if redirect_resp:
        return redirect_resp

    forced = bool(request.session.get(_PARENT_MUST_CHANGE_KEY))

    if request.method == "POST":
        current = request.POST.get("current_password") or ""
        password = request.POST.get("password") or ""
        confirm = request.POST.get("password_confirm") or ""

        errors = []
        if not forced and parent.authenticate_portal(current) != PORTAL_AUTH_PASSWORD:
            # Only the real password re-authorises a voluntary change. Accepting
            # the temporary one here would let someone holding a recovery email
            # walk past this check without ever triggering the forced flow.
            errors.append("La contraseña actual no es correcta.")
        elif password != confirm:
            errors.append("Las contraseñas no coinciden.")
        elif not forced and password == current:
            errors.append("La contraseña nueva debe ser distinta de la actual.")
        elif forced and parent.authenticate_portal(password) == PORTAL_AUTH_TEMPORARY:
            # The forced flow exists BECAUSE the credential in play is a
            # plaintext temporary password sitting in an inbox — keeping it as
            # the permanent one defeats the whole point (and the email tells the
            # family "la temporal deja de funcionar"). The voluntary flow's
            # `password == current` guard cannot catch this because `current` is
            # deliberately not asked for here.
            errors.append("La contraseña nueva debe ser distinta de la contraseña temporal.")
        else:
            try:
                validate_password(password, password_validators=_parent_password_validators())
            except ValidationError as exc:
                errors.extend(exc.messages)

        if errors:
            for message in errors:
                messages.error(request, message)
            return render(
                request,
                "parent_portal/change_password.html",
                _password_rules_context(parent, forced=forced),
            )

        # Clears `temporary_password` as a side effect, so the credential that
        # is sitting in the family's inbox stops working right here. Also bumps
        # `portal_credential_changed_at`, invalidating every OTHER portal session.
        parent.set_portal_password(password)
        request.session.pop(_PARENT_MUST_CHANGE_KEY, None)
        # Re-stamp THIS session with the new credential marker so the device
        # that just changed the password is not logged out by its own change.
        request.session[_PARENT_CRED_STAMP_KEY] = _credential_stamp(parent)
        # Cycle the key on a credential change, the same reason login does:
        # anything that captured the old session id must not keep the account.
        request.session.cycle_key()

        messages.success(request, "✅ Contraseña actualizada.")
        return redirect("parent_portal_dashboard")

    return render(
        request,
        "parent_portal/change_password.html",
        _password_rules_context(parent, forced=forced),
    )


@require_http_methods(["POST", "GET"])
def parent_portal_logout(request):
    # Full flush, not just popping parent_id: the session may still hold other
    # state and "cerrar sesión" should mean the session is gone.
    request.session.flush()
    return redirect("parent_portal_login")


# ── Portal surface ──────────────────────────────────────────────────────────


def parent_portal_dashboard(request):
    parent, redirect_resp = _require_parent(request)
    if redirect_resp:
        return redirect_resp

    # No `prefetch_related("enrollments")`: dashboard.html renders only the name, age
    # and group, so it was one wasted query per page load.
    children = parent.children.select_related("group").order_by("first_name")
    today = date.today()
    upcoming = (
        Payment.objects.filter(parent=parent, payment_status="pending", due_date__gte=today)
        .select_related("student")
        .order_by("due_date")[:5]
    )

    # Offer a tax certificate only for years the family actually paid something
    # in — the PDF generator happily produces an empty certificate for any year
    # in range, and a wall of buttons back to 2020 that all download a blank
    # page is worse than no button at all. One aggregate, not a year loop.
    paid_range = Payment.objects.filter(
        parent=parent, payment_status="completed", payment_date__isnull=False
    ).aggregate(first=Min("payment_date"), last=Max("payment_date"))
    if paid_range["first"] and paid_range["last"]:
        certificate_years = list(range(paid_range["last"].year, paid_range["first"].year - 1, -1))
    else:
        # Nothing paid yet: still offer the current year so the button exists
        # and the family can see the feature is there.
        certificate_years = [today.year]

    return render(
        request,
        "parent_portal/dashboard.html",
        {
            "parent": parent,
            "children": children,
            "upcoming_payments": upcoming,
            "certificate_years": certificate_years,
            "now": timezone.now(),
        },
    )


def parent_portal_payments(request):
    parent, redirect_resp = _require_parent(request)
    if redirect_resp:
        return redirect_resp

    # Never trust query-string ints. Parsing alone was not enough: `?year=foo`
    # was caught, but `?year=-5` and `?year=99999999999` still reached the
    # `due_date__year` lookup, where Django builds date bounds and raises
    # ValueError / OverflowError — a 500 from a hand-edited URL.
    year = safe_int(request.GET.get("year"), default=date.today().year, low=MIN_QUERY_YEAR, high=MAX_QUERY_YEAR)

    payments = (
        Payment.objects.filter(parent=parent, due_date__year=year)
        .select_related("student", "enrollment")
        .order_by("-due_date")
    )
    return render(
        request,
        "parent_portal/payments.html",
        {"parent": parent, "payments": payments, "year": year},
    )


def parent_portal_receipt(request, payment_id: int):
    """
    Download a receipt PDF for a payment — must belong to the current parent.
    """
    parent, redirect_resp = _require_parent(request)
    if redirect_resp:
        return redirect_resp

    payment = get_object_or_404(
        Payment.objects.select_related("student", "parent"),
        id=payment_id,
        parent=parent,
    )

    from billing.services.pdf_service import generate_payment_receipt

    pdf_bytes = generate_payment_receipt(payment)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="recibo-{payment.id}.pdf"'
    return response


def parent_portal_tax_certificate(request):
    """Download the tax certificate for the current year."""
    parent, redirect_resp = _require_parent(request)
    if redirect_resp:
        return redirect_resp

    year = safe_int(request.GET.get("year"), default=date.today().year, low=MIN_QUERY_YEAR, high=MAX_QUERY_YEAR)

    from billing.services.pdf_service import generate_tax_certificate

    pdf_bytes = generate_tax_certificate(parent, year)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="certificado-fiscal-{year}.pdf"'
    return response


__all__ = [
    "parent_portal_dashboard",
    "parent_portal_forgot_password",
    "parent_portal_login",
    "parent_portal_logout",
    "parent_portal_payments",
    "parent_portal_receipt",
    "parent_portal_change_password",
    "parent_portal_tax_certificate",
    "send_portal_temporary_password",
    "send_portal_invitation_once",
]
