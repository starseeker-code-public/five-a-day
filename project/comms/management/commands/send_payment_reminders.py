"""
Management command wrapping `comms.tasks.send_payment_reminders`.

Lets external schedulers (Cloud Scheduler → Cloud Run Job in production, plain
cron elsewhere) trigger the weekly payment reminders without Celery Beat.

Usage:
    python manage.py send_payment_reminders
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send payment reminder emails (and opt-in SMS) for payments due within 7 days"

    def handle(self, *args, **options):
        from comms.tasks import send_payment_reminders

        result = send_payment_reminders.apply().get()
        self.stdout.write(self.style.SUCCESS(f"Payment reminders: {result}"))
