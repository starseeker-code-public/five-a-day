"""Core Celery tasks."""

from datetime import timedelta

from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone

logger = get_task_logger(__name__)


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
