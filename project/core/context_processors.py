import logging
from datetime import date

from django.conf import settings

from .constants import SCHEDULED_APPS
from .models import HistoryLog, TodoItem

logger = logging.getLogger(__name__)


def today_notifications(request):
    today = date.today()

    # Teacher-role flags for template-level gating of admin-only UI. THE SAME
    # predicate the middleware enforces with — computed independently, the two
    # disagreed for an authenticated user with no Teacher row (the UI trimmed
    # itself while the middleware treated the session as admin, or vice versa).
    from core.middleware import _is_non_admin_teacher

    user = getattr(request, "user", None)
    teacher = None
    if user is not None and getattr(user, "is_authenticated", False):
        teacher = getattr(user, "teacher", None)
    session = getattr(request, "session", None)
    session_authenticated = bool(session is not None and session.get("is_authenticated"))
    is_non_admin_teacher = _is_non_admin_teacher(request)
    # `not is_non_admin_teacher` FAILED OPEN: this context processor runs on
    # EVERY render, including pages served to an anonymous visitor (the login
    # page, the 403/404/500 handlers, anything rendered before a session
    # exists), and for those `_is_non_admin_teacher` answers False — "not a
    # non-admin teacher" — which the template then read as "is an admin" and
    # rendered the admin UI to nobody in particular. Admin is a POSITIVE grant:
    # it now requires an authenticated session first.
    is_admin_user = session_authenticated and not is_non_admin_teacher

    # QA testing tools visibility — logged-in ADMIN Teacher in the testing
    # environment only (non-admin teachers must not see the dev tools).
    # `active` matters as much as `admin`: deactivating a Teacher is how this
    # academy offboards somebody, and the dev tools include DB seed/reset.
    show_testing_tools = settings.IS_TESTING_ENV and teacher is not None and teacher.admin and teacher.active

    # The header bell and the actions-history feed are admin-only, so a
    # non-admin teacher never renders either one — don't spend the queries.
    if is_non_admin_teacher:
        return {
            "notifications_today_todos": [],
            "notifications_today_apps": [],
            "notifications_count": 0,
            "history_count": 0,
            "support_email": getattr(settings, "SUPPORT_EMAIL", ""),
            "show_testing_tools": show_testing_tools,
            "is_admin_user": is_admin_user,
            "is_non_admin_teacher": is_non_admin_teacher,
            "drive_receipts_url": getattr(settings, "GOOGLE_DRIVE_RECEIPTS_URL", ""),
        }

    # Todos due today
    # This runs on EVERY page, so it degrades rather than 500s — but it used to
    # degrade silently, which meant a database problem showed up only as an
    # empty sidebar that looked like "no todos today".
    try:
        todos = list(TodoItem.objects.filter(due_date=today).values("id", "text"))
    except Exception:
        logger.exception("Could not load today's todos; rendering the sidebar without them")
        todos = []

    # Scheduled apps that run today
    apps_today = []
    for app in SCHEDULED_APPS:
        if not app.get("active"):
            continue
        if app["frequency"] == "every_friday" and today.weekday() == 4:
            apps_today.append(app)
        elif app["frequency"] == "monthly_day_1" and today.day == 1:
            apps_today.append(app)

    notifications_count = len(todos) + len(apps_today)

    # History log count
    try:
        history_count = HistoryLog.objects.count()
    except Exception:
        logger.exception("Could not count history entries; rendering the badge as 0")
        history_count = 0

    return {
        "notifications_today_todos": todos,
        "notifications_today_apps": apps_today,
        "notifications_count": notifications_count,
        "history_count": history_count,
        "support_email": getattr(settings, "SUPPORT_EMAIL", ""),
        "show_testing_tools": show_testing_tools,
        "is_admin_user": is_admin_user,
        "is_non_admin_teacher": is_non_admin_teacher,
        "drive_receipts_url": getattr(settings, "GOOGLE_DRIVE_RECEIPTS_URL", ""),
    }


def csp_nonce(request):
    """Expose the per-request CSP nonce minted by SecurityHeadersMiddleware.

    Every inline `<script>` block in the templates carries
    `nonce="{{ csp_nonce }}"`; a block without it shows up as a violation
    report while `CSP_ENFORCE` is off, and will not execute once it is on.
    """
    return {"csp_nonce": getattr(request, "csp_nonce", "")}
