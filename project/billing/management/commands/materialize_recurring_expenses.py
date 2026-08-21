"""
Management command wrapping the recurring-expense materialization tasks.

Lets external schedulers (Cloud Scheduler → Cloud Run Job in production, plain
cron elsewhere) materialize recurring expense templates without Celery Beat.

Usage:
    python manage.py materialize_recurring_expenses                  # monthly templates (1st-of-month job)
    python manage.py materialize_recurring_expenses --month 3 --year 2027
    python manage.py materialize_recurring_expenses --daily          # weekly + yearly templates (daily job)
    python manage.py materialize_recurring_expenses --daily --date 2027-03-15
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Materialize recurring expense templates (monthly by default, weekly/yearly with --daily)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--daily",
            action="store_true",
            help="Materialize WEEKLY + YEARLY templates for today (the daily job). Default is MONTHLY templates.",
        )
        parser.add_argument(
            "--month", type=int, default=None, help="Month for the monthly run (1-12). Defaults to current."
        )
        parser.add_argument("--year", type=int, default=None, help="Year for the monthly run. Defaults to current.")
        parser.add_argument("--date", type=str, default=None, help="ISO date for the --daily run. Defaults to today.")

    def handle(self, *args, **options):
        from billing.tasks import (
            materialize_recurring_expenses_daily_task,
            materialize_recurring_expenses_task,
        )

        if options["daily"]:
            if options["month"] or options["year"]:
                raise CommandError("--month/--year only apply to the monthly run (omit --daily)")
            result = materialize_recurring_expenses_daily_task.apply(kwargs={"target_date": options["date"]}).get()
        else:
            if options["date"]:
                raise CommandError("--date only applies to the daily run (add --daily)")
            result = materialize_recurring_expenses_task.apply(
                kwargs={"month": options["month"], "year": options["year"]}
            ).get()

        self.stdout.write(self.style.SUCCESS(f"Recurring expenses: {result}"))
