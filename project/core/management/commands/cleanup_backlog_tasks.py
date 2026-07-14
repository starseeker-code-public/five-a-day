"""
Management command wrapping `core.tasks.cleanup_done_backlog_tasks`.

Lets external schedulers (cron on the testing VM; the QA backlog is a
testing-environment feature) delete old done backlog tasks without Celery Beat.

Usage:
    python manage.py cleanup_backlog_tasks
    python manage.py cleanup_backlog_tasks --days 60
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Delete QA backlog tasks that have been marked done for more than --days days"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30, help="Age threshold in days (default 30)")

    def handle(self, *args, **options):
        from core.tasks import cleanup_done_backlog_tasks

        result = cleanup_done_backlog_tasks.apply(kwargs={"days": options["days"]}).get()
        self.stdout.write(self.style.SUCCESS(f"Backlog cleanup: {result}"))
