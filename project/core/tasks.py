"""Core Celery tasks."""

from datetime import timedelta

from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone

logger = get_task_logger(__name__)

# Two years keeps a full academic-year comparison available while bounding the
# table. Long enough to answer "who changed this student's fee last September?".
AUDIT_LOG_RETENTION_DAYS = 730


@shared_task(name="core.tasks.cleanup_done_backlog_tasks")
def cleanup_done_backlog_tasks(days: int = 30):
    """Delete QA backlog tasks that have been marked 'done' for over `days` days.

    Scheduled daily by Celery Beat. `updated_at` is the time the task was last
    changed, i.e. when it was marked done, so it stands in for a completion date.
    """
    from core.models import BacklogTask

    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = BacklogTask.objects.filter(status="done", updated_at__lt=cutoff).delete()
    logger.info("Deleted %d completed backlog task(s) older than %d days", deleted, days)
    return {"status": "success", "deleted": deleted}


@shared_task(name="core.tasks.prune_audit_log")
def prune_audit_log(days: int = AUDIT_LOG_RETENTION_DAYS):
    """Delete AuditLog rows older than `days`.

    The audit trail had no cap and no pruning of any kind, unlike HistoryLog
    (capped at 1,000 rows). It grows fast — scheduling one student's academic
    year of payments writes 16 rows on its own — so at the documented 2,000
    student scale it would become the largest table in the database with
    nothing ever removing a row.
    """
    from core.models import AuditLog

    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = AuditLog.objects.filter(created_at__lt=cutoff).delete()
    logger.info("Deleted %d audit log row(s) older than %d days", deleted, days)
    return {"status": "success", "deleted": deleted}


@shared_task(name="core.tasks.purge_expired_sessions")
def purge_expired_sessions():
    """Delete expired `django_session` rows.

    Nothing purged this table before v1.23.0, and that matters more than
    ordinary table growth because it holds authentication material:
    `django_session` is the DEFAULT database session backend, and session
    payloads are base64-encoded JSON — signed, not encrypted. Anything a view
    puts in the session is readable by anyone who can read the table, and rows
    outlived their cookies indefinitely.

    Django ships `clearsessions` for this; the task wraps it so the work is
    scheduled the same way as every other periodic job.

    It used to purge `parent_session_tokens` too. That table is gone: the parent
    portal issues a temporary PASSWORD rather than a set-password link, and the
    hash lives in a column on `parents` that `set_portal_password` clears — so
    there is no longer a side table of spent credentials to sweep.
    """
    from django.core.management import call_command

    call_command("clearsessions")

    logger.info("Purged expired sessions")
    return {"status": "success"}
