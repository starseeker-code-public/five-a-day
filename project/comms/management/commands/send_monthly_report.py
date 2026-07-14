"""
Management command wrapping `comms.tasks.send_monthly_report_task`.

Lets external schedulers (Cloud Scheduler → Cloud Run Job in production, plain
cron elsewhere) trigger the monthly financial report without Celery Beat.

Usage:
    python manage.py send_monthly_report
    python manage.py send_monthly_report --recipient someone@example.com
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Email the monthly financial report (defaults to settings.SUPPORT_EMAIL)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--recipient",
            type=str,
            default=None,
            help="Override the recipient email. Defaults to settings.SUPPORT_EMAIL.",
        )

    def handle(self, *args, **options):
        from comms.tasks import send_monthly_report_task

        result = send_monthly_report_task.apply(kwargs={"recipient_email": options["recipient"]}).get()
        self.stdout.write(self.style.SUCCESS(f"Monthly report: {result}"))
