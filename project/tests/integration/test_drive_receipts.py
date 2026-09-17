"""Integration tests for the Drive receipt archive task + backfill (v1.29.0).

The Drive client is never contacted — `DriveReceiptService` is patched. These
assert the task's guard rails (environment, config, payment state) and the
backfill command's dry-run/apply behaviour against real Payment rows.

Note every "it uploads" test has to declare `PRODUCTION`: since v1.29.5 the
archive is production-only (the QA VM opts in per-toggle, development never),
and the suite runs as development — so the DEFAULT answer everywhere here is
"no upload", which is the point of the gate.
"""

from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from billing.models import Payment
from core.models import QAConfiguration
from core.services import drive_service
from core.services.drive_service import (
    TESTING_SUBFOLDER,
    DriveReceiptService,
    DriveUploadResult,
    archive_subfolder,
    drive_uploads_allowed,
)
from core.tasks import upload_receipt_to_drive_task
from core.views.payments import _queue_payment_receipt

pytestmark = pytest.mark.django_db

#: The archive is production-only; everything that expects an upload says so.
PRODUCTION = override_settings(ENVIRONMENT="production")
#: The QA VM. `IS_TESTING_ENV` is computed at import from DJANGO_ENV + DEBUG, so
#: it has to be overridden directly rather than via ENVIRONMENT.
QA_VM = override_settings(ENVIRONMENT="testing", IS_TESTING_ENV=True)


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
    def test_development_never_archives(self, student_with_parent, active_enrollment, site_config):
        """The suite runs as development, so the task refuses before it even
        looks at the credentials — a developer clicking "marcar cobrado" must not
        file a fictional receipt into the academy's real archive."""

        p = _completed_payment(student_with_parent, student_with_parent.parents.first(), active_enrollment)
        with patch("core.tasks.get_drive_service") as get_service:
            get_service.return_value.is_configured.return_value = True
            result = upload_receipt_to_drive_task.apply(args=[p.id]).get()
        assert result["status"] == "disabled"
        get_service.return_value.upload_receipt.assert_not_called()

    @PRODUCTION
    def test_not_configured_is_a_noop(self, student_with_parent, active_enrollment, site_config):
        p = _completed_payment(student_with_parent, student_with_parent.parents.first(), active_enrollment)
        # The suite has no GOOGLE_DRIVE_RECEIPTS_FOLDER_ID → not configured.
        result = upload_receipt_to_drive_task.apply(args=[p.id]).get()
        assert result["status"] == "not_configured"

    @PRODUCTION
    def test_skips_a_non_completed_payment(self, student_with_parent, active_enrollment, site_config):
        p = _completed_payment(student_with_parent, student_with_parent.parents.first(), active_enrollment)
        Payment.objects.filter(pk=p.pk).update(payment_status="pending")
        with patch("core.tasks.get_drive_service") as get_service:
            get_service.return_value.is_configured.return_value = True
            result = upload_receipt_to_drive_task.apply(args=[p.id]).get()
        assert result["status"] == "skipped"
        get_service.return_value.upload_receipt.assert_not_called()

    @PRODUCTION
    def test_uploads_a_completed_payment(self, student_with_parent, active_enrollment, site_config):
        p = _completed_payment(student_with_parent, student_with_parent.parents.first(), active_enrollment)
        with patch("core.tasks.get_drive_service") as get_service:
            svc = get_service.return_value
            svc.is_configured.return_value = True
            svc.upload_receipt.return_value = DriveUploadResult(
                success=True, status="uploaded", folder_path="Curso 2026/2027/Recibos/Septiembre 26", file_id="F"
            )
            result = upload_receipt_to_drive_task.apply(args=[p.id]).get()
        assert result["status"] == "uploaded"
        svc.upload_receipt.assert_called_once()

    @PRODUCTION
    def test_missing_payment_is_reported_not_raised(self):
        with patch("core.tasks.get_drive_service") as get_service:
            get_service.return_value.is_configured.return_value = True
            result = upload_receipt_to_drive_task.apply(args=[999999]).get()
        assert result["status"] == "error"

    def test_completing_a_payment_enqueues_the_drive_upload(
        self, django_capture_on_commit_callbacks, student_with_parent, active_enrollment, site_config
    ):
        """The wiring: `_queue_payment_receipt` (fired on every pending→completed
        transition) dispatches BOTH the receipt email and the Drive upload, in
        independent try/excepts."""

        p = _completed_payment(student_with_parent, student_with_parent.parents.first(), active_enrollment)
        with (
            patch("core.tasks.upload_receipt_to_drive_task.delay") as drive_delay,
            patch("comms.tasks.send_payment_receipt_email_task.delay") as email_delay,
        ):
            with django_capture_on_commit_callbacks(execute=True):
                _queue_payment_receipt(p.id)
        drive_delay.assert_called_once_with(p.id)
        email_delay.assert_called_once_with(p.id)


class TestBackfillCommand:
    def test_refuses_outside_production(self, student_with_parent, active_enrollment, site_config):
        """Up front, not once per payment: a refusal per row reads like a Drive
        outage rather than a switched-off feature."""
        with pytest.raises(CommandError, match="switched off for this environment"):
            call_command("backfill_drive_receipts", "--apply")

    @PRODUCTION
    def test_dry_run_uploads_nothing(self, student_with_parent, active_enrollment, site_config):
        _completed_payment(student_with_parent, student_with_parent.parents.first(), active_enrollment)
        with patch("billing.management.commands.backfill_drive_receipts.DriveReceiptService") as Svc:
            Svc.return_value.is_configured.return_value = True
            out = StringIO()
            call_command("backfill_drive_receipts", stdout=out)
            Svc.return_value.upload_receipt.assert_not_called()
        assert "Would upload" in out.getvalue()

    @PRODUCTION
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

    @PRODUCTION
    def test_not_configured_errors_out(self, student_with_parent, active_enrollment, site_config):
        """A CommandError, so the Cloud Run Job exits non-zero. Writing to stderr
        and returning cleanly reported success for a run that archived nothing."""
        with patch("billing.management.commands.backfill_drive_receipts.DriveReceiptService") as Svc:
            Svc.return_value.is_configured.return_value = False
            with pytest.raises(CommandError, match="not configured"):
                call_command("backfill_drive_receipts", "--apply")


class TestEnvironmentGate:
    """`drive_uploads_allowed()` — the one predicate the task, the service and the
    backfill command all read. Uploading from anywhere but production writes into
    the academy's real, permanent archive, so the default answer is no."""

    @PRODUCTION
    def test_production_always_archives(self):
        assert drive_uploads_allowed() is True
        # No sandbox level: production files receipts straight into the month.
        assert archive_subfolder() == ""

    @PRODUCTION
    def test_production_ignores_the_qa_toggle(self):
        """The flag is QA's, not a production kill switch — production must keep
        archiving whatever the row on the QA database happens to say."""

        config = QAConfiguration.get_config()
        config.drive_uploads_enabled = False
        config.save()
        assert drive_uploads_allowed() is True

    def test_development_never_archives_even_with_the_flag_on(self):
        config = QAConfiguration.get_config()
        config.drive_uploads_enabled = True
        config.save()
        assert drive_uploads_allowed() is False

    @QA_VM
    def test_qa_vm_is_off_by_default(self):
        assert drive_uploads_allowed() is False

    @QA_VM
    def test_qa_vm_archives_into_the_sandbox_when_the_toggle_is_on(self):
        config = QAConfiguration.get_config()
        config.drive_uploads_enabled = True
        config.save()
        assert drive_uploads_allowed() is True
        assert archive_subfolder() == TESTING_SUBFOLDER

    @QA_VM
    def test_a_database_error_fails_closed(self):
        """The archive is best-effort and never raises, so an unreadable toggle
        must mean "do not write to the academy's archive", not "assume yes"."""

        with patch("core.models.QAConfiguration.get_config", side_effect=RuntimeError("db down")):
            assert drive_service.drive_uploads_allowed() is False


class TestSandboxPath:
    """The QA VM files into `<Mes> YY/testing/`, production into `<Mes> YY/`."""

    def _service(self):
        svc = DriveReceiptService(base_folder_id="BASE")
        # Pre-seed the credential sentinel so `is_configured()` is True without a
        # real service account; the Drive client itself is stubbed below.
        svc._credentials = {"fake": "creds"}
        return svc

    def _upload(self, svc, payment):
        # Each level resolves to an id named after it, so the assertion is about
        # the SEQUENCE of folders asked for, not about Drive.
        api = _FakeDriveApi()
        with (
            patch.object(svc, "_get_service", return_value=api),
            patch.object(svc, "_find_or_create_folder", side_effect=lambda s, name, parent: f"id:{name}") as find,
            patch.object(svc, "_existing_receipt", return_value=None),
        ):
            result = svc.upload_receipt(payment, b"%PDF")
        return result, [call.args[1] for call in find.call_args_list], api

    @PRODUCTION
    def test_production_path_has_no_testing_level(self, student_with_parent, active_enrollment, site_config):
        p = _completed_payment(student_with_parent, student_with_parent.parents.first(), active_enrollment)
        svc = self._service()
        result, levels, api = self._upload(svc, p)
        assert levels == ["Curso 2026/2027", "Recibos", "Septiembre 26"]
        assert result.folder_path == "Curso 2026/2027/Recibos/Septiembre 26"
        assert result.status == "uploaded"
        assert api.created_parents == ["id:Septiembre 26"]

    @QA_VM
    def test_qa_path_ends_in_the_testing_folder(self, student_with_parent, active_enrollment, site_config):
        config = QAConfiguration.get_config()
        config.drive_uploads_enabled = True
        config.save()

        p = _completed_payment(student_with_parent, student_with_parent.parents.first(), active_enrollment)
        svc = self._service()
        result, levels, api = self._upload(svc, p)
        assert levels == ["Curso 2026/2027", "Recibos", "Septiembre 26", "testing"]
        assert result.folder_path == "Curso 2026/2027/Recibos/Septiembre 26/testing"
        # And the file is created in the sandbox folder, not the month folder.
        assert api.created_parents == ["id:testing"]

    @QA_VM
    def test_qa_toggle_off_uploads_nothing(self, student_with_parent, active_enrollment, site_config):
        p = _completed_payment(student_with_parent, student_with_parent.parents.first(), active_enrollment)
        svc = self._service()
        with patch.object(svc, "_get_service") as get_service:
            result = svc.upload_receipt(p, b"%PDF")
        assert result.status == "disabled"
        assert result.success is False
        # It must not even build a Drive client outside production.
        get_service.assert_not_called()


class _FakeDriveApi:
    """The two `files()` calls `upload_receipt` makes, recorded not performed."""

    def __init__(self):
        self.created_parents = []

    def files(self):
        return self

    def create(self, body=None, media_body=None, fields=None, supportsAllDrives=None):
        self.created_parents.append(body["parents"][0])
        return self

    def execute(self):
        return {"id": "FILE"}
