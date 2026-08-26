"""
Management command wrapping `core.tasks.prune_audit_log`.

Production runs on Cloud Run with no Celery Beat process, so every Beat task
needs a command wrapper that Cloud Scheduler can invoke as a Cloud Run Job.

Usage:
    python manage.py prune_audit_log
    python manage.py prune_audit_log --days 365
"""

from django.core.management.base import BaseCommand

from core.tasks import AUDIT_LOG_RETENTION_DAYS


class Command(BaseCommand):
    help = "Delete AuditLog rows older than --days days (default: two years)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=AUDIT_LOG_RETENTION_DAYS,
            help=f"Age threshold in days (default {AUDIT_LOG_RETENTION_DAYS})",
        )

    def handle(self, *args, **options):
        from core.tasks import prune_audit_log

        result = prune_audit_log.apply(kwargs={"days": options["days"]}).get()
        self.stdout.write(self.style.SUCCESS(f"Audit log pruned: {result}"))
