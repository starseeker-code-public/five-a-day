"""
Configuración de Celery para Five a Day
https://docs.celeryq.dev/en/stable/django/first-steps-with-django.html
"""

import os

from celery import Celery
from celery.schedules import crontab

# Establecer el módulo de configuración de Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")

app = Celery("fiveaday")

# Usar configuración de Django con prefijo CELERY_
app.config_from_object("django.conf:settings", namespace="CELERY")

# Autodescubrir tareas en todas las apps instaladas
app.autodiscover_tasks()


# ============================================================================
# CELERY BEAT SCHEDULE - Tareas programadas
# ============================================================================
app.conf.beat_schedule = {
    # Birthday emails — daily at 8:00 AM (Europe/Madrid)
    "send-birthday-emails-daily": {
        "task": "comms.tasks.send_birthday_emails_task",
        "schedule": crontab(hour=8, minute=0),
        "options": {"queue": "emails"},
    },
    # Payment reminders — every Monday at 9:00 AM
    "send-payment-reminders-weekly": {
        "task": "comms.tasks.send_payment_reminders",
        "schedule": crontab(hour=9, minute=0, day_of_week=1),
        "options": {"queue": "emails"},
    },
    # v1.4 — Monthly payment generation on the 1st at 06:00
    "generate-monthly-payments": {
        "task": "billing.tasks.generate_monthly_payments_task",
        "schedule": crontab(hour=6, minute=0, day_of_month=1),
        "options": {"queue": "celery"},
    },
    # v1.5 — Materialize MONTHLY recurring expense templates on the 1st at 06:30
    "materialize-recurring-expenses": {
        "task": "billing.tasks.materialize_recurring_expenses_task",
        "schedule": crontab(hour=6, minute=30, day_of_month=1),
        "options": {"queue": "celery"},
    },
    # Materialize WEEKLY + YEARLY recurring expense templates — daily at 06:15
    "materialize-recurring-expenses-daily": {
        "task": "billing.tasks.materialize_recurring_expenses_daily_task",
        "schedule": crontab(hour=6, minute=15),
        "options": {"queue": "celery"},
    },
    # v1.4 — Monthly report on the 28th at 20:00
    "send-monthly-report": {
        "task": "comms.tasks.send_monthly_report_task",
        "schedule": crontab(hour=20, minute=0, day_of_month=28),
        "options": {"queue": "emails"},
    },
    # Fun Friday announcements — drain due FunFridayScheduledSend rows daily at
    # 14:30 (rows are scheduled for Monday 14:30, so this fires them on time)
    "send-due-fun-friday-emails": {
        "task": "comms.tasks.send_due_fun_friday_emails_task",
        "schedule": crontab(hour=14, minute=30),
        "options": {"queue": "emails"},
    },
    # QA backlog housekeeping — delete tasks done for >30 days, daily at 07:00
    "cleanup-done-backlog-tasks": {
        "task": "core.tasks.cleanup_done_backlog_tasks",
        "schedule": crontab(hour=7, minute=0),
        "options": {"queue": "celery"},
    },
    # Audit-trail retention — drop rows older than 2 years, weekly Sunday 03:00.
    # Without this the table only ever grows (see core.tasks.prune_audit_log).
    "prune-audit-log": {
        "task": "core.tasks.prune_audit_log",
        "schedule": crontab(hour=3, minute=0, day_of_week=0),
        "options": {"queue": "celery"},
    },
    # Expired sessions + spent parent magic-link tokens, daily 03:30. Both
    # tables hold authentication material and nothing purged either of them
    # before v1.23.0 (see core.tasks.purge_expired_sessions).
    "purge-expired-sessions": {
        "task": "core.tasks.purge_expired_sessions",
        "schedule": crontab(hour=3, minute=30),
        "options": {"queue": "celery"},
    },
}

app.conf.timezone = "Europe/Madrid"


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Tarea de debug para verificar que Celery funciona"""
    print(f"Request: {self.request!r}")
