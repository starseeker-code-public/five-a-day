import logging
from datetime import date

from django.conf import settings

from core.decorators import may_use_qa_tools
from core.middleware import _is_non_admin_teacher, _is_tester_teacher

from .constants import SCHEDULED_APPS
from .models import HistoryLog, TodoItem

logger = logging.getLogger(__name__)


def today_notifications(request):
    today = date.today()

    # Teacher-role flags for template-level gating of admin-only UI. THE SAME
    # predicate the middleware enforces with — computed independently, the two
    # disagreed for an authenticated user with no Teacher row (the UI trimmed
    # itself while the middleware treated the session as admin, or vice versa).

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
    is_tester_user = _is_tester_teacher(request)
    # `is_admin_user` gates the UI, not the enforcement: the sidebar's Pagos /
    # Gastos / Aplicaciones / Informes / Base de Datos links, the notifications
    # bell, the actions-history dropdown and the per-view help. A tester is
    # `admin=False`, so without the second clause it would be allowed through to
    # every one of those URLs by `TESTER_ALLOWED_URL_NAMES` while the navigation
    # to them stayed hidden — reachable only by typing the address, which is not
    # a demonstration of anything.
    #
    # The name is now slightly wider than it reads, so: this variable means
    # "render the full interface". What a session may actually DO is decided by
    # the middleware allowlist and `@admin_required`, never here, and the tester
    # allowlist is chosen to cover exactly what these links point at.
    is_admin_user = session_authenticated and (not is_non_admin_teacher or is_tester_user)

    # QA testing tools visibility — the SAME predicate `qa_access_required`
    # enforces (testing env + active admin Teacher), so the icon and the gate
    # cannot drift apart.
    show_testing_tools = may_use_qa_tools(teacher)

    # The header bell and the actions-history feed are admin-only, so a
    # non-admin teacher never renders either one — don't spend the queries.
    if is_non_admin_teacher and not is_tester_user:
        return {
            "notifications_today_todos": [],
            "notifications_today_apps": [],
            "notifications_count": 0,
            "history_count": 0,
            "support_email": getattr(settings, "SUPPORT_EMAIL", ""),
            "show_testing_tools": show_testing_tools,
            "is_admin_user": is_admin_user,
            "is_non_admin_teacher": is_non_admin_teacher,
            "is_tester_user": is_tester_user,
            "tester_login_notice": getattr(settings, "TESTER_LOGIN_NOTICE", ""),
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
        "is_tester_user": is_tester_user,
        "tester_login_notice": getattr(settings, "TESTER_LOGIN_NOTICE", ""),
        "drive_receipts_url": getattr(settings, "GOOGLE_DRIVE_RECEIPTS_URL", ""),
    }


def csp_nonce(request):
    """Expose the per-request CSP nonce minted by SecurityHeadersMiddleware.

    Every inline `<script>` block in the templates carries
    `nonce="{{ csp_nonce }}"`; a block without it shows up as a violation
    report while `CSP_ENFORCE` is off, and will not execute once it is on.
    """
    return {"csp_nonce": getattr(request, "csp_nonce", "")}


def app_prefix(request):
    """Expose the app's mount point ("/app") to every template.

    `base.html` renders it onto `<body data-app-prefix>`, which is where
    `base.js` reads `window.APP_PREFIX` from. It exists so the JS modules have
    ONE owner for the prefix instead of sixteen copies of the literal — see the
    note beside that assignment. Templates themselves should keep using
    `{% url %}`, which already resolves through the prefix.
    """
    return {"app_path_prefix": settings.APP_PATH_PREFIX}
