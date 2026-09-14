"""Unit tests for the Google Drive receipt archive (v1.29.0).

The Drive client is mocked — these assert the folder-path logic, idempotency,
and the guarantee that the service NEVER raises whatever Drive does.

Since v1.29.5 the archive is production-only, so every test that expects an
upload has to say it is production (the `_production` autouse fixture below).
`override_settings` cannot decorate a plain pytest class — only a
`SimpleTestCase` — hence a fixture rather than a class decorator.
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.services import drive_service
from core.services.drive_service import (
    DriveReceiptService,
    curso_folder_name,
    month_folder_name,
    receipt_filename,
)


def _payment(pid=633, d=date(2026, 9, 3), first="Ana", last="Ruiz"):
    return SimpleNamespace(
        id=pid,
        payment_date=d,
        due_date=None,
        # `Payment.receipt_date` — the same property that picks the YEAR of the
        # receipt number, so the archive folder and the number cannot disagree.
        receipt_date=d,
        student=SimpleNamespace(first_name=first, last_name=last),
    )


class TestFolderNames:
    @pytest.mark.parametrize(
        "d,expected",
        [
            (date(2026, 9, 3), "Curso 2026/2027"),  # September → new course
            (date(2026, 8, 1), "Curso 2026/2027"),  # August → already the new course
            (date(2026, 7, 31), "Curso 2025/2026"),  # July → still the old course
            (date(2027, 1, 10), "Curso 2026/2027"),  # January → same course as prev Sept
            (date(2027, 6, 30), "Curso 2026/2027"),
        ],
    )
    def test_curso_boundary_is_august(self, d, expected):
        assert curso_folder_name(d) == expected

    @pytest.mark.parametrize(
        "d,expected",
        [
            (date(2026, 9, 3), "Septiembre 26"),
            (date(2026, 1, 5), "Enero 26"),
            (date(2027, 12, 31), "Diciembre 27"),
        ],
    )
    def test_month_folder_name(self, d, expected):
        assert month_folder_name(d) == expected


class TestReceiptFilename:
    def test_id_prefixed_and_sanitized(self):
        p = _payment(pid=633, first="Ana", last="Ruiz Pérez")
        # spaces collapse to dashes, id leads (so the upload is idempotent by id).
        assert receipt_filename(p) == "633_Ana_Ruiz-Pérez.pdf"

    def test_slashes_removed(self):
        p = _payment(first="A/B", last="C\\D")
        assert "/" not in receipt_filename(p)
        assert "\\" not in receipt_filename(p)


class TestEnvironmentGate:
    """`drive_uploads_allowed()` / `archive_subfolder()` — the pair every caller
    reads. No database: outside the QA VM the answer is decided by the
    environment alone, so a dev box cannot reach the toggle even in principle."""

    def test_production_is_allowed_with_no_sandbox_level(self, settings):
        settings.ENVIRONMENT = "production"
        assert drive_service.drive_uploads_allowed() is True
        assert drive_service.archive_subfolder() == ""

    def test_development_is_refused(self, settings):
        settings.ENVIRONMENT = "development"
        settings.IS_TESTING_ENV = False
        assert drive_service.drive_uploads_allowed() is False

    def test_outside_production_the_sandbox_level_is_testing(self, settings):
        settings.ENVIRONMENT = "testing"
        assert drive_service.archive_subfolder() == drive_service.TESTING_SUBFOLDER


class TestUploadReceipt:
    @pytest.fixture(autouse=True)
    def _production(self, settings):
        """These assert what an upload DOES; the environment gate has its own
        class above. Without this every one of them would return "disabled"."""
        settings.ENVIRONMENT = "production"

    def _svc(self):
        # Configured: base folder + a (patched) credential.
        return DriveReceiptService(base_folder_id="BASE")

    def test_not_configured_returns_cleanly(self):
        svc = DriveReceiptService(base_folder_id="")
        result = svc.upload_receipt(_payment(), b"%PDF")
        assert result.status == "not_configured"
        assert result.success is False
        # Path is still computed so logs/UX can name the intended location.
        assert result.folder_path == "Curso 2026/2027/Recibos/Septiembre 26"

    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_uploaded_when_folders_exist(self, _info):
        svc = self._svc()
        fake = MagicMock()
        # curso, recibos, month all found; existing-receipt check empty.
        fake.files.return_value.list.return_value.execute.side_effect = [
            {"files": [{"id": "C"}]},
            {"files": [{"id": "R"}]},
            {"files": [{"id": "M"}]},
            {"files": []},
        ]
        fake.files.return_value.create.return_value.execute.return_value = {"id": "FILE"}
        with patch.object(svc, "_get_service", return_value=fake):
            result = svc.upload_receipt(_payment(), b"%PDF")
        assert result.status == "uploaded"
        assert result.file_id == "FILE"
        # Exactly one create() → the file, no folders created.
        assert fake.files.return_value.create.call_count == 1

    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_idempotent_skips_existing(self, _info):
        svc = self._svc()
        fake = MagicMock()
        fake.files.return_value.list.return_value.execute.side_effect = [
            {"files": [{"id": "C"}]},
            {"files": [{"id": "R"}]},
            {"files": [{"id": "M"}]},
            {"files": [{"id": "E", "name": "633_Ana_Ruiz.pdf"}]},
        ]
        with patch.object(svc, "_get_service", return_value=fake):
            result = svc.upload_receipt(_payment(pid=633), b"%PDF")
        assert result.status == "skipped_exists"
        assert result.file_id == "E"
        fake.files.return_value.create.assert_not_called()

    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_contains_match_does_not_confuse_id_5_with_15(self, _info):
        svc = self._svc()
        fake = MagicMock()
        # Month folder holds payment 15's receipt; we are uploading payment 5.
        fake.files.return_value.list.return_value.execute.side_effect = [
            {"files": [{"id": "C"}]},
            {"files": [{"id": "R"}]},
            {"files": [{"id": "M"}]},
            {"files": [{"id": "OTHER", "name": "15_Bob_Diaz.pdf"}]},
        ]
        fake.files.return_value.create.return_value.execute.return_value = {"id": "FILE5"}
        with patch.object(svc, "_get_service", return_value=fake):
            result = svc.upload_receipt(_payment(pid=5), b"%PDF")
        # 15_ must NOT count as an existing receipt for payment 5.
        assert result.status == "uploaded"

    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_creates_missing_folders(self, _info):
        svc = self._svc()
        fake = MagicMock()
        # Each miss costs a second list: after creating, the service re-lists and
        # keeps the OLDEST match, so two instances racing on the same month folder
        # converge instead of forking the archive.
        fake.files.return_value.list.return_value.execute.side_effect = [
            {"files": []},  # curso missing
            {"files": [{"id": "C"}]},  # re-list after create: we won
            {"files": []},  # recibos missing
            {"files": [{"id": "R"}]},
            {"files": []},  # month missing
            {"files": [{"id": "M"}]},
            {"files": []},  # existing-receipt check
        ]
        fake.files.return_value.create.return_value.execute.side_effect = [
            {"id": "C"},
            {"id": "R"},
            {"id": "M"},
            {"id": "FILE"},
        ]
        with patch.object(svc, "_get_service", return_value=fake):
            result = svc.upload_receipt(_payment(), b"%PDF")
        assert result.status == "uploaded"
        # 3 folders + 1 file.
        assert fake.files.return_value.create.call_count == 4

    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_concurrent_folder_create_converges_on_the_oldest(self, _info):
        """Drive allows duplicate folder names and find-then-create is not atomic,
        so two instances can each create "Septiembre 26" at the start of a month.
        Whoever loses must still WRITE to the winner's folder, or the archive
        forks and the per-payment idempotency check stops seeing existing
        receipts."""
        svc = self._svc()
        fake = MagicMock()
        fake.files.return_value.list.return_value.execute.side_effect = [
            {"files": [{"id": "C"}]},
            {"files": [{"id": "R"}]},
            {"files": []},  # month not there yet...
            # ...so we create it — but the re-list shows another instance got
            # there first (oldest wins).
            {"files": [{"id": "M_WINNER"}, {"id": "M_OURS"}]},
            {"files": []},  # existing-receipt check, in the winner's folder
        ]
        fake.files.return_value.create.return_value.execute.side_effect = [
            {"id": "M_OURS"},
            {"id": "FILE"},
        ]
        with patch.object(svc, "_get_service", return_value=fake):
            result = svc.upload_receipt(_payment(), b"%PDF")

        assert result.status == "uploaded"
        parents = fake.files.return_value.create.call_args_list[-1].kwargs["body"]["parents"]
        assert parents == ["M_WINNER"]

    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_error_never_raises(self, _info):
        svc = self._svc()
        with patch.object(svc, "_get_service", side_effect=RuntimeError("boom")):
            result = svc.upload_receipt(_payment(), b"%PDF")
        assert result.success is False
        assert result.status == "error"

    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_http_403_is_named_as_a_sharing_problem(self, _info):
        from googleapiclient.errors import HttpError

        svc = self._svc()
        resp = SimpleNamespace(status=403, reason="Forbidden")
        fake = MagicMock()
        fake.files.return_value.list.return_value.execute.side_effect = HttpError(resp, b"{}")
        with patch.object(svc, "_get_service", return_value=fake):
            result = svc.upload_receipt(_payment(), b"%PDF")
        assert result.status == "error"
        assert "permiso" in result.error.lower()


class TestGetService:
    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    @patch("googleapiclient.discovery.build", return_value="DRIVE_CLIENT")
    @patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value="CREDS")
    def test_builds_and_caches_the_client(self, _creds, build, _info):
        svc = DriveReceiptService(base_folder_id="BASE")
        assert svc._get_service() == "DRIVE_CLIENT"
        assert svc._get_service() == "DRIVE_CLIENT"  # cached — not rebuilt
        build.assert_called_once()

    @patch.object(drive_service, "_service_account_info", return_value=None)
    def test_raises_when_no_credentials(self, _info):
        svc = DriveReceiptService(base_folder_id="BASE")
        with pytest.raises(RuntimeError):
            svc._get_service()

    def test_module_singleton(self):
        from core.services.drive_service import get_service

        assert get_service() is get_service()


class TestFolderCache:
    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_second_lookup_is_cached(self, _info):
        svc = DriveReceiptService(base_folder_id="BASE")
        fake = MagicMock()
        fake.files.return_value.list.return_value.execute.return_value = {"files": [{"id": "X"}]}
        first = svc._find_or_create_folder(fake, "Curso 2026/2027", "BASE")
        second = svc._find_or_create_folder(fake, "Curso 2026/2027", "BASE")
        assert first == second == "X"
        # The second resolution comes from the cache — Drive is queried once.
        fake.files.return_value.list.assert_called_once()


class TestDescribeError:
    def test_404_points_at_the_folder_id(self):
        from googleapiclient.errors import HttpError

        msg = drive_service._describe_error(HttpError(SimpleNamespace(status=404, reason="NF"), b"{}"))
        assert "no encontrada" in msg.lower()

    def test_transient_http_is_labelled_transient(self):
        from googleapiclient.errors import HttpError

        msg = drive_service._describe_error(HttpError(SimpleNamespace(status=503, reason="x"), b"{}"))
        assert "transitorio" in msg.lower()

    def test_other_http_status(self):
        from googleapiclient.errors import HttpError

        msg = drive_service._describe_error(HttpError(SimpleNamespace(status=418, reason="teapot"), b"{}"))
        assert "418" in msg

    def test_non_http_error_returns_type_name(self):
        assert drive_service._describe_error(ValueError("boom")) == "ValueError"
