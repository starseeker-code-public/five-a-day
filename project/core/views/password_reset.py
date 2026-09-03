"""
Password-reset flow — standalone, public, Teacher-auth only.

We subclass Django's built-in views so we get the signed token/uid machinery
for free, and just swap in our branded templates. The email is delivered via
Django's default `send_mail` backend (SMTP in prod, locmem in tests), with an
HTML alternative rendered from `emails/password_reset.html` so the styling
matches every other Five-a-Day transactional email.

All four URLs are exempt from `SimpleAuthMiddleware` (see core/middleware.py),
because a teacher who can't log in still needs to be able to reach them.

This module also owns the two authenticated password entry points that reuse
the same machinery: `send_password_setup_email` (fired when an admin creates a
teacher, so the account arrives with a link instead of an instruction) and
`change_password` (the "Cambiar contraseña" button on /management/).
"""

import json
import logging

from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import (
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.http import JsonResponse
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_http_methods

from core.audit_models import AuditLog
from core.rate_limit import rate_limit

logger = logging.getLogger(__name__)

UserModel = get_user_model()


class ActivationFriendlyPasswordResetForm(PasswordResetForm):
    """Password-reset form that also serves accounts awaiting activation.

    Django's default `get_users()` skips users whose password is unusable, and
    its own docstring points at subclassing as the way to change that. That
    default silently broke the documented onboarding flow here: both
    `seed_teachers` (when TEACHER_SEED_<N>_PASSWORD is omitted) and the
    "create teacher" screen provision the account with `set_unusable_password()`
    and tell the teacher to activate via /password-reset/ — but the reset page
    reported "check your inbox" while sending nothing at all, leaving the
    account permanently unreachable.

    Inactive users are still excluded: `is_active=False` is a deliberate
    lock-out, whereas an unusable password just means "not set yet".
    """

    def get_users(self, email):
        email_field_name = UserModel.get_email_field_name()
        return UserModel._default_manager.filter(**{f"{email_field_name}__iexact": email, "is_active": True})


# 3 requests / 15 min / IP. This endpoint is unauthenticated, public (it is in
# SimpleAuthMiddleware.PUBLIC_PREFIXES so a locked-out teacher can reach it), and
# it SENDS AN EMAIL on every hit — so before v1.23.0 anyone could loop a known
# teacher address and (a) bury them in reset mail as cover for a social-
# engineering attempt, and (b) burn the Gmail account's ~500/day send quota,
# which is a SHARED resource: exhaust it and payment reminders, receipts,
# welcome and birthday emails all stop, silently, because non-critical mail is
# sent with fail_silently=True.
#
# The window is 15 min rather than the usual 60 s because a legitimate user asks
# for a reset once, and Django itself applies no throttle of any kind here.
@method_decorator(rate_limit("password_reset", limit=3, window_seconds=900), name="dispatch")
class BrandedPasswordResetView(PasswordResetView):
    """Request-a-reset: user enters their email."""

    form_class = ActivationFriendlyPasswordResetForm
    template_name = "registration/password_reset_form.html"
    # Plain-text body (required by PasswordResetForm.save) — kept minimal because
    # the HTML alternative below is what users actually see in GUI clients.
    email_template_name = "registration/password_reset_email.txt"
    html_email_template_name = "emails/password_reset.html"
    subject_template_name = "registration/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")
    token_generator = default_token_generator


class BrandedPasswordResetDoneView(PasswordResetDoneView):
    """Confirmation page — tells the user to check their inbox."""

    template_name = "registration/password_reset_done.html"


class BrandedPasswordResetConfirmView(PasswordResetConfirmView):
    """Form for entering the new password (linked from the email)."""

    template_name = "registration/password_reset_confirm.html"
    success_url = reverse_lazy("password_reset_complete")


class BrandedPasswordResetCompleteView(PasswordResetCompleteView):
    """Success page — confirms the password was changed."""

    template_name = "registration/password_reset_complete.html"


# ── Password setup for a newly created teacher ───────────────────────

# Templates for the activation flavour of the reset email. Same signed
# uid/token machinery, different wording: a teacher who has just been created
# never "requested a password reset", and the reset copy ("si no has solicitado
# este cambio, ignora este mensaje") actively tells them to ignore the only
# link that can activate their account.
_SETUP_SUBJECT_TEMPLATE = "registration/teacher_activation_subject.txt"
_SETUP_EMAIL_TEMPLATE = "registration/teacher_activation_email.txt"
_SETUP_HTML_TEMPLATE = "emails/teacher_activation.html"


def send_password_setup_email(request, email: str) -> bool:
    """Email a "choose your password" link to `email`. True iff a mail went out.

    Creating a teacher used to send NOTHING: the account was provisioned with
    an unusable password and the UI simply told the admin to tell the teacher to
    go to /password-reset/ themselves. Nobody does that, so every teacher
    created in the app looked broken on first login.

    Deliberately reuses `ActivationFriendlyPasswordResetForm` — a freshly
    created account has an unusable password, which Django's stock
    `PasswordResetForm.get_users()` filters out (see that class's docstring).

    Returns False rather than raising when no active user matches or SMTP is
    down: the Teacher row is already committed by the time we get here, and
    losing the account because the mail server blinked would be worse than
    falling back to the manual /password-reset/ instruction.
    """
    form = ActivationFriendlyPasswordResetForm({"email": email})
    if not form.is_valid():
        return False
    if not list(form.get_users(form.cleaned_data["email"])):
        return False
    try:
        form.save(
            request=request,
            use_https=request.is_secure(),
            token_generator=default_token_generator,
            subject_template_name=_SETUP_SUBJECT_TEMPLATE,
            email_template_name=_SETUP_EMAIL_TEMPLATE,
            html_email_template_name=_SETUP_HTML_TEMPLATE,
        )
    except Exception:
        # Never echo the exception text (SMTP errors carry host/credential
        # detail) — log it and let the caller degrade to the manual flow.
        logger.exception("Could not send the password-setup email for a new teacher")
        return False
    return True


# ── Change your own password (authenticated) ─────────────────────────


def can_change_own_password(request) -> bool:
    """True iff the current session may use the /management/ password button.

    Two exclusions, both because the password is not what authenticates the
    session:

    - **Google OAuth sessions** (`google_authenticated`): identity comes from
      Google, and `_ensure_oauth_superuser` may well have linked to a Teacher
      that *does* carry a usable password — so the flag, not the password
      state, is what has to gate this.
    - **Accounts with no usable password**: the development env-var login
      (`_ensure_dev_user`) and any teacher who has not activated yet.
      `PasswordChangeForm` needs the current password, which they cannot give.
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if request.session.get("google_authenticated"):
        return False
    return user.has_usable_password()


# 5 attempts / 5 min / IP. The endpoint takes the CURRENT password, so an
# unthrottled version is an online password oracle against a live session.
@require_http_methods(["POST"])
@rate_limit("password_change", limit=5, window_seconds=300)
def change_password(request):
    """API: change the logged-in user's own password."""
    if not can_change_own_password(request):
        return JsonResponse(
            {
                "success": False,
                "message": "Esta cuenta no gestiona su contraseña desde aquí.",
            },
            status=403,
        )

    try:
        data = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"success": False, "message": "Petición no válida."}, status=400)
    # A valid JSON body that is not an object (`[1]`, `"x"`) parses fine and
    # then blows up on .get() with a 500.
    if not isinstance(data, dict):
        return JsonResponse({"success": False, "message": "Petición no válida."}, status=400)

    form = PasswordChangeForm(
        user=request.user,
        data={
            "old_password": data.get("current_password") or "",
            "new_password1": data.get("new_password") or "",
            "new_password2": data.get("confirm_password") or "",
        },
    )
    if not form.is_valid():
        # Django's own validator messages (localised to Spanish by
        # LANGUAGE_CODE) — written for end users, unlike raw exception text.
        return JsonResponse(
            {
                "success": False,
                "message": " ".join(m for msgs in form.errors.values() for m in msgs),
            },
            status=400,
        )

    user = form.save()
    # Changing the password rotates the session auth hash, which would log the
    # user straight out of the tab they are standing in without this.
    update_session_auth_hash(request, user)

    AuditLog.record(
        action="update",
        instance=user,
        changes={"password": "changed"},
        actor=user,
    )

    return JsonResponse({"success": True, "message": "Contraseña actualizada correctamente."})
