"""
Management command wrapping `comms.tasks.send_due_fun_friday_emails_task`.

Drains every `FunFridayScheduledSend` row whose scheduled time has passed.
This is how scheduled Fun Friday announcements actually go out in production
(Cloud Scheduler → Cloud Run Job, daily at 14:30 Europe/Madrid) — Celery Beat
covers dev/testing.

Usage:
    python manage.py send_due_fun_friday_emails
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send every Fun Friday announcement whose scheduled time has passed"

    def handle(self, *args, **options):
        from comms.tasks import send_due_fun_friday_emails_task

        result = send_due_fun_friday_emails_task.apply().get()
        self.stdout.write(self.style.SUCCESS(f"Fun Friday drain: {result}"))
