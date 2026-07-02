"""
Middleware — authentication, QA error reporting, and teacher view whitelisting.
"""

import logging
import traceback

from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.shortcuts import redirect
from django.urls import resolve, reverse

logger = logging.getLogger(__name__)


class QAErrorEmailMiddleware:
    """
    When QAConfiguration.error_email_enabled is True, catches unhandled
    exceptions and sends a detailed report to SUPPORT_EMAIL.
    Must be placed AFTER SecurityMiddleware and BEFORE other app middleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return response

    def process_exception(self, request, exception):
        try:
            from core.models import QAConfiguration

            config = QAConfiguration.get_config()
            if not config.error_email_enabled:
                return None

            support_email = getattr(settings, "SUPPORT_EMAIL", None)
            if not support_email:
                return None

            tb = traceback.format_exception(type(exception), exception, exception.__traceback__)
            tb_str = "".join(tb)

            username = request.session.get("username", "anonymous") if hasattr(request, "session") else "—"
            method = request.method
            path = request.get_full_path()
            body_preview = ""
            try:
                body_preview = request.body[:500].decode("utf-8", errors="replace")
            except Exception:
                pass

            subject = f"[ERROR] {type(exception).__name__} at {path}"
            body = (
                f"AUTOMATED ERROR REPORT — Five a Day QA\n"
                f"{'=' * 60}\n\n"
                f"Exception:   {type(exception).__name__}: {exception}\n"
                f"Path:        {method} {path}\n"
                f"User:        {username}\n"
                f"Version:     {settings.APP_VERSION}\n"
                f"Environment: {settings.ENVIRONMENT}\n"
                f"Debug:       {settings.DEBUG}\n"
                f"Server time: {__import__('datetime').datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
                f"REQUEST BODY (first 500 chars):\n"
                f"{body_preview or '(empty)'}\n\n"
                f"TRACEBACK:\n"
                f"{'-' * 60}\n"
                f"{tb_str}\n"
                f"{'=' * 60}\n"
            )

            send_mail(
                subject=subject,
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[support_email],
                fail_silently=True,
            )
        except Exception:
            logger.exception("QAErrorEmailMiddleware failed to send error email")

        return None  # Let Django's default error handling continue


# URL name whitelist for non-admin teachers. Any URL name not in this set is
# blocked. Admin-only write endpoints inside the management page are called out
# explicitly (the list view itself — `management` — is allowed so non-admins
# can see it in view-only mode). Keep this list in sync with core/urls.py and
# the per-app urls.py files.
NON_ADMIN_ALLOWED_URL_NAMES = frozenset(
    {
        # Auth + public
        "login",
        "logout",
        "google_oauth_redirect",
        "google_oauth_callback",
        "password_reset",
        "password_reset_done",
        "password_reset_confirm",
        "password_reset_complete",
        "health_check",
        # Dashboard
        "home",
        # Students (list, detail, create, update — full access per user request)
        "students_list",
        "student_create",
        "student_detail",
        "student_update",
        "search_students",
        # Waiting list (v1.1) — same authority level as regular student management
        "waiting_list",
        "assign_from_waiting_list",
        "add_to_waiting_list",
        # Parents
        "parent_create",
        # Fun Friday (attendance view + attendance API)
        "fun_friday_view",
        "add_fun_friday_attendance",
        "remove_fun_friday_attendance",
        "toggle_fun_friday_this_week",
        # Management page — view-only. Write endpoints below are NOT in this list:
        #   update_site_config, create_teacher, create_group,
        #   update_enrollment_modality  (admin-only writes)
        "management",
        "api_get_teachers",
        "language_cheque_students",
        # Expenses (v1.5) — visible to non-admin teachers for read + create
        "expenses_list",
        "create_expense",
        "delete_expense",
        # Reports (v1.7) — read-only for non-admin teachers
        "reports_view",
        "reports_pdf",
        # Todos, history, support
        "create_todo",
        "complete_todo",
        "history_list",
        "submit_support_ticket",
        # Error test pages (harmless)
        "test_error_400",
        "test_error_403",
        "test_error_404",
        "test_error_405",
        "test_error_500",
    }
)


def _is_non_admin_teacher(request) -> bool:
    """True iff the request's auth.User is linked to a Teacher with admin=False."""
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or user.is_staff:
        return False
    # Reverse accessor defined in students.Teacher.user's related_name="teacher".
    teacher = getattr(user, "teacher", None)
    if teacher is None:
        return False
    return not teacher.admin


class SimpleAuthMiddleware:
    """
    Enforces two layers of access control:

    1. Authentication: every non-public URL requires an authenticated session
       (either via env-var basic-auth in dev, Teacher login in testing/prod, or
       Google OAuth). Unauthenticated requests are redirected to /login/.
    2. Authorization: requests made by a non-admin teacher are restricted to
       the URL-name whitelist above. Blocked routes → 403 (or a redirect to the
       dashboard with a flash message for non-API routes).
    """

    PUBLIC_PREFIXES = (
        "/health/",
        "/static/",
        "/media/",
        "/auth/google/",
        "/password-reset/",
        "/parent/",  # v1.9: parent portal uses its own magic-link session
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        login_url = reverse("login")
        path = request.path
        is_public = path == login_url or any(path.startswith(prefix) for prefix in self.PUBLIC_PREFIXES)

        # Layer 1: authentication
        if not is_public and not request.session.get("is_authenticated"):
            return redirect("login")

        # Layer 2: non-admin teacher whitelist
        if not is_public and _is_non_admin_teacher(request):
            try:
                url_name = resolve(path).url_name
            except Exception:
                url_name = None

            if url_name not in NON_ADMIN_ALLOWED_URL_NAMES:
                # AJAX / API endpoints: return a plain 403 JSON response so the
                # frontend sees a real error instead of an HTML redirect body.
                if path.startswith("/api/"):
                    from django.http import JsonResponse

                    return JsonResponse(
                        {"success": False, "error": "No tienes permiso para esta acción."},
                        status=403,
                    )
                messages.error(request, "❌ No tienes permiso para acceder a esa sección.")
                return redirect("home")

        return self.get_response(request)
