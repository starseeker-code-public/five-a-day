"""Integration tests for the Drive receipt archive task + backfill (v1.29.0).

The Drive client is never contacted — `DriveReceiptService` is patched. These
assert the task's guard rails (config, payment state) and the backfill command's
dry-run/apply behaviour against real Payment rows.
"""

from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

from billing.models import Payment
from core.services.drive_service import DriveUploadResult

pytestmark = pytest.mark.django_db


def _completed_payment(student, parent, enrollment):
    return Payment.objects.create(
        student=student,
        parent=parent,
        enrollment=enrollment,
        payment_type="monthly",
        payment_method="transfer",
        amount=Decimal("54.00"),
        payment_status="completed",
        due_date=date(2026, 9, 30),
        payment_date=date(2026, 9, 3),
        concept="Mensualidad Septiembre",
    )


class TestUploadTask:
    def test_not_configured_is_a_noop(self, student_with_parent, active_enrollment, site_config):
        from comms.tasks import upload_receipt_to_drive_task

        p = _completed_payment(student_with_parent, student_with_parent.parents.first(), active_enrollment)
        # Dev/test has no GOOGLE_DRIVE_RECEIPTS_FOLDER_ID → not configured.
        result = upload_receipt_to_drive_task.apply(args=[p.id]).get()
        assert result["status"] == "not_configured"

    def test_skips_a_non_completed_payment(self, student_with_parent, active_enrollment, site_config):
        from comms.tasks import upload_receipt_to_drive_task

        p = _completed_payment(student_with_parent, student_with_parent.parents.first(), active_enrollment)
        Payment.objects.filter(pk=p.pk).update(payment_status="pending")
        with patch("core.services.drive_service.get_service") as get_service:
            get_service.return_value.is_configured.return_value = True
            result = upload_receipt_to_drive_task.apply(args=[p.id]).get()
        assert result["status"] == "skipped"
        get_service.return_value.upload_receipt.assert_not_called()

    def test_uploads_a_completed_payment(self, student_with_parent, active_enrollment, site_config):
        from comms.tasks import upload_receipt_to_drive_task

        p = _completed_payment(student_with_parent, student_with_parent.parents.first(), active_enrollment)
        with patch("core.services.drive_service.get_service") as get_service:
            svc = get_service.return_value
            svc.is_configured.return_value = True
            svc.upload_receipt.return_value = DriveUploadResult(
                success=True, status="uploaded", folder_path="Curso 2026/2027/Recibos/Septiembre 26", file_id="F"
            )
            result = upload_receipt_to_drive_task.apply(args=[p.id]).get()
        assert result["status"] == "uploaded"
        svc.upload_receipt.assert_called_once()

    def test_missing_payment_is_reported_not_raised(self):
        from comms.tasks import upload_receipt_to_drive_task

        with patch("core.services.drive_service.get_service") as get_service:
            get_service.return_value.is_configured.return_value = True
            result = upload_receipt_to_drive_task.apply(args=[999999]).get()
        assert result["status"] == "error"

    def test_completing_a_payment_enqueues_the_drive_upload(
        self, django_capture_on_commit_callbacks, student_with_parent, active_enrollment, site_config
    ):
        """The wiring: `_queue_payment_receipt` (fired on every pending→completed
        transition) dispatches BOTH the receipt email and the Drive upload, in
        independent try/excepts."""
        from core.views.payments import _queue_payment_receipt

        p = _completed_payment(student_with_parent, student_with_parent.parents.first(), active_enrollment)
        with (
            patch("comms.tasks.upload_receipt_to_drive_task.delay") as drive_delay,
            patch("comms.tasks.send_payment_receipt_email_task.delay") as email_delay,
        ):
            with django_capture_on_commit_callbacks(execute=True):
                _queue_payment_receipt(p.id)
        drive_delay.assert_called_once_with(p.id)
        email_delay.assert_called_once_with(p.id)


class TestBackfillCommand:
    def test_dry_run_uploads_nothing(self, student_with_parent, active_enrollment, site_config):
        _completed_payment(student_with_parent, student_with_parent.parents.first(), active_enrollment)
        with patch("billing.management.commands.backfill_drive_receipts.DriveReceiptService") as Svc:
            Svc.return_value.is_configured.return_value = True
            out = StringIO()
            call_command("backfill_drive_receipts", stdout=out)
            Svc.return_value.upload_receipt.assert_not_called()
        assert "Would upload" in out.getvalue()

    def test_apply_uploads_each(self, student_with_parent, active_enrollment, site_config):
        _completed_payment(student_with_parent, student_with_parent.parents.first(), active_enrollment)
        with (
            patch("billing.management.commands.backfill_drive_receipts.DriveReceiptService") as Svc,
            patch("billing.management.commands.backfill_drive_receipts.generate_payment_receipt", return_value=b"%PDF"),
        ):
            Svc.return_value.is_configured.return_value = True
            Svc.return_value.upload_receipt.return_value = DriveUploadResult(success=True, status="uploaded")
            out = StringIO()
            call_command("backfill_drive_receipts", "--apply", stdout=out)
            Svc.return_value.upload_receipt.assert_called_once()
        assert "1 uploaded" in out.getvalue()

    def test_not_configured_errors_out(self, student_with_parent, active_enrollment, site_config):
        with patch("billing.management.commands.backfill_drive_receipts.DriveReceiptService") as Svc:
            Svc.return_value.is_configured.return_value = False
            err = StringIO()
            call_command("backfill_drive_receipts", "--apply", stderr=err)
        assert "not configured" in err.getvalue().lower()
