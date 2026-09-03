"""
Management command wrapping `core.tasks.prune_audit_log`.

Production runs on Cloud Run with no Celery Beat process, so every Beat task
needs a command wrapper that Cloud Scheduler can invoke as a Cloud Run Job.

Usage:
    python manage.py prune_audit_log
    python manage.py prune_audit_log --days 500
    python manage.py prune_audit_log --dry-run
"""

from django.core.management.base import BaseCommand, CommandError

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
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many rows would be deleted without deleting them",
        )

    def handle(self, *args, **options):
        from core.tasks import prune_audit_log

        try:
            result = prune_audit_log.apply(kwargs={"days": options["days"], "dry_run": options["dry_run"]}).get()
        except ValueError as e:
            # The task guards against a retention window short enough to erase
            # the recent trail. Surface it as a CommandError so Cloud Run Jobs
            # report a clean failure instead of a Celery traceback.
            raise CommandError(str(e)) from e

        self.stdout.write(self.style.SUCCESS(f"Audit log pruned: {result}"))
