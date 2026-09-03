"""
Management command wrapping `core.tasks.purge_expired_sessions`.

Production has no Celery Beat process — Cloud Scheduler triggers Cloud Run Jobs
that run management commands — so every Beat entry needs a wrapper like this
one. See DEPLOYMENT.md's schedule table.

Usage:
    python manage.py purge_sessions
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Delete expired sessions and spent parent portal password tokens"

    def handle(self, *args, **options):
        from core.tasks import purge_expired_sessions

        result = purge_expired_sessions.apply().get()
        self.stdout.write(self.style.SUCCESS(f"Session purge: {result}"))
