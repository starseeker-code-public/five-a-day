"""
Management command wrapping `comms.tasks.send_birthday_emails_task`.

Lets external schedulers (Cloud Scheduler → Cloud Run Job in production, plain
cron elsewhere) trigger the daily birthday emails without a Celery Beat process.

Usage:
    python manage.py send_birthday_emails
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send birthday emails to every active student whose birthday is today"

    def handle(self, *args, **options):
        from comms.tasks import send_birthday_emails_task

        result = send_birthday_emails_task.apply().get()
        self.stdout.write(self.style.SUCCESS(f"Birthday emails: {result}"))
