"""
Password-reset flow — standalone, public, Teacher-auth only.

We subclass Django's built-in views so we get the signed token/uid machinery
for free, and just swap in our branded templates. The email is delivered via
Django's default `send_mail` backend (SMTP in prod, locmem in tests), with an
HTML alternative rendered from `emails/password_reset.html` so the styling
matches every other Five-a-Day transactional email.

All four URLs are exempt from `SimpleAuthMiddleware` (see core/middleware.py),
because a teacher who can't log in still needs to be able to reach them.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import (
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator

from core.rate_limit import rate_limit

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
