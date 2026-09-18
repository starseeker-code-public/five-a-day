"""Back-fill the Google Drive receipt archive for already-completed payments.

The on-completion upload (v1.29.0) only covers payments completed from now on.
This one-off pushes the receipts for payments that were already completed before
the feature existed. It is **idempotent** — the upload service skips a payment
whose receipt is already in its month folder — so it is safe to re-run.

    python manage.py backfill_drive_receipts                 # DRY RUN — count only
    python manage.py backfill_drive_receipts --apply         # actually upload
    python manage.py backfill_drive_receipts --apply --academic-year 2026-2027
    python manage.py backfill_drive_receipts --apply --limit 50

Best-effort per payment: one failure is reported and the run continues, so a
single un-renderable payment or a transient Drive blip does not abort the rest.

Subject to the same environment gate as the on-completion upload: production
always, the QA VM only while `/testing/`'s "Recibos a Drive" toggle is on (and
then into the month's `testing/` sandbox), development never. It refuses up
front rather than reporting a refusal per payment.
"""

from django.core.management.base import BaseCommand, CommandError

from billing.models import Payment
from billing.services.pdf_service import generate_payment_receipt
from core.services.drive_service import DriveReceiptService, drive_uploads_allowed


class Command(BaseCommand):
    help = "Upload receipts for already-completed payments to the Google Drive archive (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Actually upload (default is a dry run).")
        parser.add_argument("--academic-year", type=str, help="Limit to one academic year, e.g. 2026-2027.")
        parser.add_argument("--limit", type=int, help="Process at most N payments (useful for a first test).")

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        academic_year = options.get("academic_year")
        limit = options.get("limit")

        # The environment gate, asked once up front. The service refuses every
        # payment individually too, but walking the whole archive to report one
        # refusal per row reads like a Drive outage rather than a switched-off
        # feature — and on the QA VM this is the message that says which switch.
        if not drive_uploads_allowed():
            raise CommandError(
                "Google Drive uploads are switched off for this environment. Production always archives; "
                "on the testing VM turn on 'Recibos a Drive' in /testing/ first (uploads then land in the "
                "month's 'testing' subfolder). Development never archives."
            )

        # One service instance for the whole run so the folder-id cache is shared
        # across every payment (one lookup per Curso/Recibos/month, not per file).
        drive = DriveReceiptService()
        if not drive.is_configured():
            # CommandError, not a message and a clean return: this runs as a Cloud
            # Run Job, and exiting 0 with an empty archive reports success for a
            # run that did nothing at all.
            raise CommandError(
                "Google Drive receipts are not configured. Set GOOGLE_DRIVE_RECEIPTS_FOLDER_ID and the "
                "service-account credentials, and share the folder with the service account as Editor."
            )

        payments = (
            Payment.objects.filter(payment_status="completed")
            # `enrollment__enrollment_type` / `enrollment__student` are what the
            # receipt's discount breakdown reads; without them the loop pays two
            # extra queries per payment across the entire archive.
            .select_related("student", "parent", "enrollment", "enrollment__enrollment_type", "enrollment__student")
            .order_by("payment_date", "id")
        )
        if academic_year:
            payments = payments.filter(enrollment__academic_year=academic_year)
        if limit:
            payments = payments[:limit]

        total = payments.count()
        mode = "APPLY" if apply_changes else "DRY RUN"
        self.stdout.write(f"[{mode}] {total} completed payment(s) to archive.")

        if not apply_changes:
            # Nothing to iterate for: the count IS the answer. Walking the rows to
            # increment a counter to the number just printed streamed the whole
            # joined archive for no information.
            self.stdout.write(f"Would upload {total} receipt(s). Re-run with --apply.")
            return

        counts = {"uploaded": 0, "skipped_exists": 0, "error": 0}
        for payment in payments.iterator():
            try:
                pdf_bytes = generate_payment_receipt(payment)
            except Exception as exc:  # noqa: BLE001 — report and continue
                counts["error"] += 1
                self.stderr.write(
                    self.style.WARNING(f"  payment {payment.id}: PDF render failed ({type(exc).__name__})")
                )
                continue

            result = drive.upload_receipt(payment, pdf_bytes)
            if result.status == "uploaded":
                counts["uploaded"] += 1
            elif result.status == "skipped_exists":
                counts["skipped_exists"] += 1
            else:
                counts["error"] += 1
                self.stderr.write(self.style.WARNING(f"  payment {payment.id}: {result.status} — {result.error}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {counts['uploaded']} uploaded, {counts['skipped_exists']} already present, "
                f"{counts['error']} failed."
            )
        )
