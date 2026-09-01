"""Core Celery tasks."""

from datetime import timedelta

from celery import shared_task
from celery.utils.log import get_task_logger
from django.db import models
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
    """Delete expired `django_session` rows and spent parent magic-link tokens.

    Nothing purged either table before v1.23.0. That matters more than ordinary
    table growth because both hold authentication material:

    - `django_session` is the DEFAULT database session backend, and session
      payloads are base64-encoded JSON — signed, not encrypted. Anything a view
      puts in the session is therefore readable by anyone who can read the
      table, and rows outlived their cookies indefinitely.
    - `parent_session_tokens` keeps every magic link ever issued, spent or not.

    Django ships `clearsessions` for the first half; this task wraps it so the
    work is scheduled the same way as every other periodic job, and adds the
    parent tokens, which Django knows nothing about.
    """
    from django.core.management import call_command

    from students.models import ParentSessionToken

    call_command("clearsessions")

    # Consumed or expired: either way the token can never authenticate again,
    # so the row is pure residue. `used_at` is set atomically on consumption
    # (see ParentSessionToken.consume_by_token).
    now = timezone.now()
    deleted, _ = ParentSessionToken.objects.filter(
        models.Q(used_at__isnull=False) | models.Q(expires_at__lt=now)
    ).delete()
    logger.info("Purged expired sessions; deleted %d spent parent token(s)", deleted)
    return {"status": "success", "parent_tokens_deleted": deleted}
