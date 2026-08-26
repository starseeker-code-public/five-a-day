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
