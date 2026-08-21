"""Django management command: export students / payments to Google Sheets."""

from django.core.management.base import BaseCommand, CommandError

from core.services.google_sheets_service import get_service


class Command(BaseCommand):
    help = "Export students and/or payments to the configured Google Sheets spreadsheet."

    def add_arguments(self, parser):
        parser.add_argument(
            "--students",
            action="store_true",
            help="Export the active students table.",
        )
        parser.add_argument(
            "--payments",
            action="store_true",
            help="Export the payments table (defaults to current academic year).",
        )
        parser.add_argument(
            "--academic-year",
            default=None,
            help='Academic year to filter payments (e.g., "2025-2026"). Default: current year.',
        )
        parser.add_argument(
            "--students-sheet",
            default="Students",
            help='Worksheet name for students. Default: "Students".',
        )
        parser.add_argument(
            "--payments-sheet",
            default="Payments",
            help='Worksheet name for payments. Default: "Payments".',
        )

    def handle(self, *args, **opts):
        do_students = opts["students"]
        do_payments = opts["payments"]
        if not (do_students or do_payments):
            # Default: export both when no flags are given.
            do_students = do_payments = True

        service = get_service()
        if not service.is_configured():
            raise CommandError(
                "Google Sheets integration is not configured. "
                "Set GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON (or _FILE) "
                "and GOOGLE_SHEETS_SPREADSHEET_ID in the environment."
            )

        failures = []

        if do_students:
            self.stdout.write(f"→ Exporting students to '{opts['students_sheet']}'…")
            result = service.export_students(worksheet_name=opts["students_sheet"])
            if result.success:
                self.stdout.write(self.style.SUCCESS(f"  ✓ {result.rows_written} rows written."))
            else:
                self.stdout.write(self.style.ERROR(f"  ✗ {result.error}"))
                failures.append(result)

        if do_payments:
            self.stdout.write(f"→ Exporting payments to '{opts['payments_sheet']}'…")
            result = service.export_payments(
                worksheet_name=opts["payments_sheet"],
                academic_year=opts["academic_year"],
            )
            if result.success:
                self.stdout.write(self.style.SUCCESS(f"  ✓ {result.rows_written} rows written."))
            else:
                self.stdout.write(self.style.ERROR(f"  ✗ {result.error}"))
                failures.append(result)

        if failures:
            raise CommandError(f"{len(failures)} export(s) failed. See errors above.")
