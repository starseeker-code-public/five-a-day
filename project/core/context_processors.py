from datetime import date

from django.conf import settings

from .constants import SCHEDULED_APPS
from .models import HistoryLog, TodoItem


def today_notifications(request):
    today = date.today()

    # Todos due today
    try:
        todos = list(TodoItem.objects.filter(due_date=today).values("id", "text"))
    except Exception:
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
        history_count = 0

    # Teacher-role flags for template-level gating of admin-only UI.
    # Anyone who isn't a linked non-admin teacher is treated as admin
    # (dev basic-auth, OAuth, and admin Teachers all count as admin).
    user = getattr(request, "user", None)
    teacher = None
    if user is not None and getattr(user, "is_authenticated", False):
        teacher = getattr(user, "teacher", None)
    is_non_admin_teacher = teacher is not None and not teacher.admin
    is_admin_user = not is_non_admin_teacher

    # QA testing tools visibility — any logged-in Teacher (admin or not) in the
    # testing environment. Gated on a real Teacher account, not a QA username.
    show_testing_tools = settings.IS_TESTING_ENV and teacher is not None

    return {
        "notifications_today_todos": todos,
        "notifications_today_apps": apps_today,
        "notifications_count": notifications_count,
        "history_count": history_count,
        "support_email": getattr(settings, "SUPPORT_EMAIL", ""),
        "show_testing_tools": show_testing_tools,
        "is_admin_user": is_admin_user,
        "is_non_admin_teacher": is_non_admin_teacher,
    }
