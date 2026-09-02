"""
Management command wrapping the GCP cost archive task.

Lets external schedulers (Cloud Scheduler → Cloud Run Job in production, plain
cron elsewhere) store a finished month's real Google Cloud spend as a concrete
`software` Expense row without Celery Beat. Idempotent — an already-archived
month is reported and skipped.

Usage:
    python manage.py archive_gcp_costs                    # the month before today
    python manage.py archive_gcp_costs --month 8 --year 2026   # backfill a month
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Archive a finished month's Google Cloud spend as a 'software' expense (previous month by default)"

    def add_arguments(self, parser):
        parser.add_argument("--month", type=int, default=None, help="Month to archive (1-12). Needs --year.")
        parser.add_argument("--year", type=int, default=None, help="Year to archive. Needs --month.")

    def handle(self, *args, **options):
        from billing.tasks import archive_gcp_costs_task

        month, year = options["month"], options["year"]
        if (month is None) != (year is None):
            raise CommandError("--month and --year must be given together (or neither).")
        if month is not None and not 1 <= month <= 12:
            raise CommandError("--month must be between 1 and 12.")

        result = archive_gcp_costs_task.apply(kwargs={"month": month, "year": year}).get()

        status = result.get("status")
        if status in ("unavailable", "locked"):
            # Non-zero exit so Cloud Run Jobs / cron flag the run and retry it.
            raise CommandError(f"GCP cost archive did not complete: {result}")
        style = self.style.SUCCESS if status == "created" else self.style.WARNING
        self.stdout.write(style(f"GCP cost archive: {result}"))
