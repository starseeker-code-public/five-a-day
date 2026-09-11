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
"""

from django.core.management.base import BaseCommand

from billing.models import Payment
from billing.services.pdf_service import generate_payment_receipt
from core.services.drive_service import DriveReceiptService


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

        # One service instance for the whole run so the folder-id cache is shared
        # across every payment (one lookup per Curso/Recibos/month, not per file).
        drive = DriveReceiptService()
        if not drive.is_configured():
            self.stderr.write(
                self.style.ERROR(
                    "Google Drive receipts are not configured. Set GOOGLE_DRIVE_RECEIPTS_FOLDER_ID and the "
                    "service-account credentials, and share the folder with the service account as Editor."
                )
            )
            return

        payments = (
            Payment.objects.filter(payment_status="completed")
            .select_related("student", "parent", "enrollment")
            .order_by("payment_date", "id")
        )
        if academic_year:
            payments = payments.filter(enrollment__academic_year=academic_year)
        if limit:
            payments = payments[:limit]

        total = payments.count()
        mode = "APPLY" if apply_changes else "DRY RUN"
        self.stdout.write(f"[{mode}] {total} completed payment(s) to archive.")

        counts = {"uploaded": 0, "skipped_exists": 0, "error": 0, "would_upload": 0}
        for payment in payments.iterator():
            if not apply_changes:
                counts["would_upload"] += 1
                continue
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

        if apply_changes:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done. {counts['uploaded']} uploaded, {counts['skipped_exists']} already present, "
                    f"{counts['error']} failed."
                )
            )
        else:
            self.stdout.write(f"Would upload {counts['would_upload']} receipt(s). Re-run with --apply.")
