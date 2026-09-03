"""
Two-factor authentication views.

Flow (admin Teachers only — the guard is a decorator below):

  1.  GET  /two-factor/setup/       → generates a secret + QR + backup codes
                                       (stored on Teacher but not yet enabled)
  2.  POST /two-factor/setup/       → user submits the first TOTP code;
                                       on success two_factor_enabled=True
  3.  GET  /two-factor/manage/      → dashboard: on/off, rotate backup codes,
                                       disable entirely
  4.  POST /two-factor/verify/      → mid-login gate: session flag
                                       `is_authenticated` is set only after
                                       this succeeds when 2FA is enabled.

The login view (`core.views.auth.login_view`) short-circuits into the
verify step whenever the authenticated user's Teacher record has
`two_factor_enabled=True`.
"""

from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.rate_limit import rate_limit
from core.services import two_factor_service as tfs

_PENDING_USER_SESSION_KEY = "_2fa_pending_user_id"


def _teacher_for(user):
    """Return the Teacher linked to `user`, or None."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "teacher", None)


def _admin_only(view):
    """Decorator: only admin Teachers can access 2FA management pages."""

    @wraps(view)
    def _wrapper(request, *args, **kwargs):
        teacher = _teacher_for(getattr(request, "user", None))
        if teacher is None or not teacher.admin:
            messages.error(request, "❌ Solo los administradores pueden gestionar 2FA.")
            return redirect("home")
        return view(request, teacher, *args, **kwargs)

    return _wrapper


# ── Setup / manage / disable (post-login) ──────────────────────────────────


@_admin_only
def two_factor_setup(request, teacher):
    """
    GET  → generate a fresh secret + QR + backup codes and render the
            enrolment page. Regenerating is safe: nothing is enabled until
            the user submits a valid code.
    POST → validate the submitted code and flip `two_factor_enabled=True`.
    """
    if request.method == "POST":
        code = (request.POST.get("code") or "").strip()
        if tfs.confirm_enrolment(teacher, code):
            messages.success(request, "✅ 2FA activado correctamente.")
            return redirect("two_factor_manage")
        messages.error(request, "❌ Código incorrecto. Vuelve a intentarlo.")
        # fall through to render the setup page again — same secret retained

    payload = tfs.begin_enrolment(teacher) if not teacher.two_factor_secret else None
    if payload is None:
        # Setup already in progress (secret exists, not yet confirmed) —
        # re-render the QR without rotating the secret so the user can
        # still scan and confirm.
        payload = _payload_from_existing(teacher)

    return render(
        request,
        "two_factor/setup.html",
        {
            "teacher": teacher,
            "qr_png_base64": payload.qr_png_base64,
            "secret": payload.secret,
            "backup_codes": payload.backup_codes,
        },
    )


def _payload_from_existing(teacher) -> tfs.EnrolmentPayload:
    """Rebuild the enrolment payload from a Teacher that already has a
    staged secret. Backup codes are NOT shown again — they were displayed
    when originally generated."""
    import base64
    import io

    import pyotp
    import qrcode

    totp = pyotp.TOTP(teacher.two_factor_secret)
    provisioning_uri = totp.provisioning_uri(name=teacher.email, issuer_name=tfs._issuer_name())
    img = qrcode.make(provisioning_uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return tfs.EnrolmentPayload(
        secret=teacher.two_factor_secret,
        provisioning_uri=provisioning_uri,
        qr_png_base64=base64.b64encode(buf.getvalue()).decode("ascii"),
        backup_codes=[],  # already shown at generation time
    )


@_admin_only
def two_factor_manage(request, teacher):
    """
    Dashboard for admins with 2FA enabled: rotate backup codes, disable.
    Redirects to setup if 2FA is not yet configured.
    """
    if not teacher.two_factor_enabled:
        return redirect("two_factor_setup")

    new_codes: list[str] = []
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "rotate":
            new_codes = tfs.rotate_backup_codes(teacher)
            messages.success(request, "✅ Códigos de respaldo regenerados. Guárdalos ahora.")
        elif action == "disable":
            # Re-authenticate the SECOND FACTOR before removing it. Without this,
            # anyone holding a live admin session cookie (an unlocked laptop)
            # could POST action=disable and then re-enrol 2FA on their own phone
            # from /two-factor/setup/ (which mints a fresh secret whenever the
            # stored one is empty) — taking over the admin's second factor with
            # no password and no code. Every other credential change in the app
            # re-authenticates first; this is the one that was missing it. A
            # current TOTP or an unused backup code both satisfy it (a lost
            # authenticator must still be removable with the backup codes).
            code = (request.POST.get("code") or "").strip()
            if not code or not tfs.verify_code(teacher, code):
                messages.error(
                    request,
                    "❌ Introduce un código actual de tu app (o un código de respaldo) para desactivar 2FA.",
                )
                return redirect("two_factor_manage")
            tfs.disable(teacher)
            messages.success(request, "✅ 2FA desactivado.")
            return redirect("home")

    return render(
        request,
        "two_factor/manage.html",
        {
            "teacher": teacher,
            "backup_codes_remaining": len(teacher.two_factor_backup_codes or []),
            "new_backup_codes": new_codes,
        },
    )


# ── Login gate (pre-session, between password check and full sign-in) ─────


@rate_limit("two_factor_verify", limit=6, window_seconds=60)
@require_http_methods(["GET", "POST"])
def two_factor_verify(request):
    """
    Second-factor gate at login time. Reached only after the password step
    stashes `_2fa_pending_user_id` in the session — a session with just
    that key is NOT yet logged in (see `SimpleAuthMiddleware` for the
    `is_authenticated` gate).
    """
    pending_user_id = request.session.get(_PENDING_USER_SESSION_KEY)
    if not pending_user_id:
        # Nothing to verify — bounce back to the login form.
        return redirect("login")

    from django.contrib.auth import get_user_model

    User = get_user_model()
    try:
        user = User.objects.select_related("teacher").get(id=pending_user_id)
    except User.DoesNotExist:
        request.session.pop(_PENDING_USER_SESSION_KEY, None)
        return redirect("login")

    teacher = getattr(user, "teacher", None)
    if teacher is None or not teacher.two_factor_enabled:
        # Not applicable — bail out to a clean login.
        request.session.pop(_PENDING_USER_SESSION_KEY, None)
        return redirect("login")

    if request.method == "POST":
        code = (request.POST.get("code") or "").strip()
        if tfs.verify_code(teacher, code):
            # Promote the pending pre-auth session into a real logged-in one.
            from core.views.auth import _finalize_session_login

            display = getattr(user, "first_name", "") or user.get_username()
            # The Google credential hand-off that used to live here is gone
            # (v1.23.0): the OAuth flow no longer requests offline access or
            # keeps any token, so there is nothing to carry across the second
            # factor. `google=` only tags the session for display purposes.
            _finalize_session_login(request, user, display)
            request.session.pop(_PENDING_USER_SESSION_KEY, None)
            return redirect("home")
        messages.error(request, "❌ Código incorrecto.")

    return render(request, "two_factor/verify.html", {"email": user.email})


__all__ = [
    "two_factor_manage",
    "two_factor_setup",
    "two_factor_verify",
    "_PENDING_USER_SESSION_KEY",
]
