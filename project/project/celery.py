"""
Configuración de Celery para Five a Day
https://docs.celeryq.dev/en/stable/django/first-steps-with-django.html
"""

import logging
import os
from contextvars import Token

from celery import Celery
from celery.schedules import crontab
from celery.signals import task_failure, task_postrun, task_prerun

from comms.log_safe import safe_log
from core.logging_utils import get_request_id, reset_request_id, sanitize_request_id, set_request_id

logger = logging.getLogger(__name__)

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
    # Archive the previous month's real GCP spend as a Software expense — on the
    # 3rd at 06:45, because the BigQuery billing export lags up to ~2 days and on
    # the 1st the closed month is still incomplete. Idempotent.
    "archive-gcp-costs": {
        "task": "billing.tasks.archive_gcp_costs_task",
        "schedule": crontab(hour=6, minute=45, day_of_month=3),
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
        "task": "core.tasks.send_due_fun_friday_emails_task",
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


# ============================================================================
# TASK LOG CONTEXT + FAILURE REPORTING
# ============================================================================
# Two gaps these close.
#
# 1. A task's log records had nothing tying them to each other or to the
#    request that queued them. Production runs CELERY_TASK_ALWAYS_EAGER, so a
#    task usually IS part of a request — hence: inherit the request's id when
#    there is one, and fall back to the task id only for work that has no
#    request behind it (Beat, a Cloud Run Job, a management command). Inheriting
#    is the point; overwriting would split one incident in two again.
#
# 2. Nothing logged a task failure in a uniform place. Every task invented its
#    own reporting, so "did the nightly job actually work?" could only be
#    answered task by task — and a task that died before reaching its own
#    handler answered it nowhere.
_TASK_ID_TOKENS: dict[str, Token[str]] = {}


def _bind_task_log_context(task_id=None, **kwargs):
    if get_request_id() or not task_id:
        return
    _TASK_ID_TOKENS[task_id] = set_request_id(sanitize_request_id(task_id))


def _unbind_task_log_context(task_id=None, **kwargs):
    token = _TASK_ID_TOKENS.pop(task_id, None)
    if token is not None:
        reset_request_id(token)


def _log_task_failure(task_id=None, exception=None, sender=None, einfo=None, **kwargs):
    """One ERROR per terminal task failure, naming the task and the cause.

    Not the arguments: they carry recipient addresses and ids (see the note in
    `comms.tasks`). The task name plus the correlation id is enough to find the
    task's own records, which have the domain context.

    `Retry` never reaches here — Celery signals a retry separately — so this
    fires once, when the task has actually given up.
    """

    logger.error(
        "Celery task '%s' failed: %s: %s",
        safe_log(getattr(sender, "name", "unknown")),
        type(exception).__name__ if exception else "unknown",
        safe_log(exception),
        exc_info=exception if exception is not None else False,
        extra={"celery_task": safe_log(getattr(sender, "name", "unknown")), "celery_task_id": safe_log(task_id)},
    )


def _connect_task_signals():
    task_prerun.connect(_bind_task_log_context, dispatch_uid="project.celery.bind_log_context")
    task_postrun.connect(_unbind_task_log_context, dispatch_uid="project.celery.unbind_log_context")
    task_failure.connect(_log_task_failure, dispatch_uid="project.celery.log_task_failure")


_connect_task_signals()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Tarea de debug para verificar que Celery funciona.

    `logger`, not `print`: production ships worker output to Cloud Logging via
    the logging config, and a bare `print` bypasses the level filter and the
    structured format — so the one task whose entire job is to prove the worker
    is alive was the one task whose output could go missing.
    """
    logger.info("debug_task ejecutada: %r", self.request)
