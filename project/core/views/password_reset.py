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

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import (
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.urls import reverse_lazy
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


class BrandedPasswordResetView(PasswordResetView):
    """Request-a-reset: user enters their email."""

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


def build_reset_link(request, user) -> str:
    """
    Build a password-reset link for a given user. Useful for tests and for the
    'send-invite' flow after admin creates a new Teacher.
    """
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse_lazy("password_reset_confirm", kwargs={"uidb64": uid, "token": token})
    scheme = "https" if request.is_secure() else "http"
    host = request.get_host() if hasattr(request, "get_host") else getattr(settings, "DEFAULT_DOMAIN", "localhost:8000")
    return f"{scheme}://{host}{path}"
