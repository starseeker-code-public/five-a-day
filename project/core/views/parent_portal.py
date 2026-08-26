"""
Parent portal (v1.9).

Magic-link authentication (email a short-lived token, session-based after
consumption). Read-only surface: dashboard, payment history, receipts,
tax certificates. Completely separate from the admin auth flow.
"""

from __future__ import annotations

import logging
from datetime import date

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from billing.models import Payment
from core.rate_limit import rate_limit
from students.models import Parent, ParentSessionToken

logger = logging.getLogger(__name__)

_PARENT_SESSION_KEY = "parent_id"
_PARENT_SESSION_MAX_AGE = 60 * 60 * 6  # 6 hours


def _current_parent(request) -> Parent | None:
    pid = request.session.get(_PARENT_SESSION_KEY)
    if not pid:
        return None
    return Parent.objects.filter(id=pid).first()


def _require_parent(request):
    parent = _current_parent(request)
    if parent is None:
        return None, redirect("parent_portal_login")
    return parent, None


# ── Login flow ──────────────────────────────────────────────────────────────


@rate_limit("parent_portal_login", limit=5, window_seconds=60)
def parent_portal_login(request):
    """
    Step 1 of the magic-link flow. GET renders the email form; POST issues a
    token, emails the link, and shows a "check your inbox" message regardless
    of whether the email exists — enumeration protection.

    v1.10: rate-limited to 5 POSTs / minute / IP.
    """
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        if not email:
            messages.error(request, "Introduce un email válido.")
            return render(request, "parent_portal/login.html")

        parent = Parent.objects.filter(email__iexact=email).first()
        if parent:
            token = ParentSessionToken.issue(parent)
            link = request.build_absolute_uri(reverse("parent_portal_verify", args=[token.token]))
            # Queue the email asynchronously. Synchronous SMTP inside the POST
            # would (a) leak "email exists" via response-time timing and defeat
            # the enumeration-protection story, and (b) let a slow Gmail auth
            # stall the "check your inbox" render.
            from comms.tasks import send_parent_magic_link_task

            try:
                send_parent_magic_link_task.delay(parent.id, link)
            except Exception:  # noqa: BLE001 — never fail the request over email
                logger.exception("Failed to enqueue magic-link email for parent %s", parent.id)
        else:
            # Deliberately not logging the address: this endpoint is built to
            # not reveal whether an email is registered, and the log is a place
            # that story leaks from.
            logger.info("Parent portal login attempted for an unregistered email")

        return render(
            request,
            "parent_portal/login_sent.html",
            {"email": email},
        )

    return render(request, "parent_portal/login.html")


@rate_limit("parent_portal_verify", limit=20, window_seconds=60, count_methods=("GET", "POST"))
@require_http_methods(["GET"])
def parent_portal_verify(request, token: str):
    """Consume the magic-link token and set the parent session. Rate-limited
    to 20/min/IP so an attacker can't iterate the 32-hex token space at
    unlimited RPS. GET-only — the magic link comes via email."""
    session_token = ParentSessionToken.consume_by_token(token)
    if session_token is None:
        messages.error(request, "Enlace inválido o caducado.")
        return redirect("parent_portal_login")

    # Start a clean session on the identity change. `flush()` gives a brand-new
    # session key, which does two things:
    #   1. Prevents session fixation — this is a login, and a pre-existing
    #      session id must never survive one (django.contrib.auth.login cycles
    #      the key for exactly this reason; this custom login has to do it too).
    #   2. Drops any admin state (`is_authenticated`) that happened to be in the
    #      same session, so a teacher clicking a parent's magic link doesn't end
    #      up holding both identities in one cookie — and so `parent_portal_logout`
    #      is a real logout rather than a partial one.
    request.session.flush()
    request.session[_PARENT_SESSION_KEY] = session_token.parent_id
    request.session.set_expiry(_PARENT_SESSION_MAX_AGE)
    return redirect("parent_portal_dashboard")


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

    children = parent.children.select_related("group").prefetch_related("enrollments").order_by("first_name")
    today = date.today()
    upcoming = (
        Payment.objects.filter(parent=parent, payment_status="pending", due_date__gte=today)
        .select_related("student")
        .order_by("due_date")[:5]
    )

    return render(
        request,
        "parent_portal/dashboard.html",
        {
            "parent": parent,
            "children": children,
            "upcoming_payments": upcoming,
            "now": timezone.now(),
        },
    )


def parent_portal_payments(request):
    parent, redirect_resp = _require_parent(request)
    if redirect_resp:
        return redirect_resp

    # Never trust query-string ints — a malformed `?year=foo` used to crash
    # the view with a 500.
    try:
        year = int(request.GET.get("year") or date.today().year)
    except (TypeError, ValueError):
        year = date.today().year

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

    try:
        year = int(request.GET.get("year") or date.today().year)
    except (TypeError, ValueError):
        year = date.today().year

    from billing.services.pdf_service import generate_tax_certificate

    pdf_bytes = generate_tax_certificate(parent, year)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="certificado-fiscal-{year}.pdf"'
    return response


__all__ = [
    "parent_portal_dashboard",
    "parent_portal_login",
    "parent_portal_logout",
    "parent_portal_payments",
    "parent_portal_receipt",
    "parent_portal_tax_certificate",
    "parent_portal_verify",
]
