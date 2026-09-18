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
from googleapiclient.errors import HttpError

from core.services import drive_service
from core.services.drive_service import (
    DriveReceiptService,
    curso_folder_name,
    get_service,
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
            (date(2026, 9, 3), "Curso 2026/27"),  # September → new course
            (date(2026, 8, 1), "Curso 2026/27"),  # August → already the new course
            (date(2026, 7, 31), "Curso 2025/26"),  # July → still the old course
            (date(2027, 1, 10), "Curso 2026/27"),  # January → same course as prev Sept
            # Two digits for the second year, matching the academy's own
            # folders. Four digits built a parallel tree beside the real one and
            # reported success (caught by a live upload, 2026-09-18).
            (date(2030, 9, 1), "Curso 2030/31"),
            (date(2029, 12, 31), "Curso 2029/30"),
            (date(2027, 6, 30), "Curso 2026/27"),
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
    """`drive_uploads_allowed()` — the one predicate every caller reads.

    Production only, and answered from the environment alone: no database, so a
    dev box cannot enable it even in principle. The QA opt-in toggle and its
    `testing/` sandbox were removed in v1.29.10 — the archive authenticates as a
    delegated Google account, and the QA VM cannot complete that consent at all
    because Google refuses a plain-HTTP redirect URI on a raw IP.
    """

    def test_production_is_allowed(self, settings):
        settings.ENVIRONMENT = "production"
        assert drive_service.drive_uploads_allowed() is True

    def test_development_is_refused(self, settings):
        settings.ENVIRONMENT = "development"
        settings.IS_TESTING_ENV = False
        assert drive_service.drive_uploads_allowed() is False

    def test_the_qa_vm_is_refused_too(self, settings):
        """Testing used to be able to opt in. It no longer can, and this is the
        test that would fail if somebody re-added a back door."""
        settings.ENVIRONMENT = "testing"
        settings.IS_TESTING_ENV = True
        assert drive_service.drive_uploads_allowed() is False

    def test_the_gate_never_touches_the_database(self, settings):
        """No `django_db` mark here: if this ever needs one, the gate has grown a
        database read and a DB blip could switch the real archive off."""
        settings.ENVIRONMENT = "production"
        assert drive_service.drive_uploads_allowed() is True


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
        assert result.folder_path == "Curso 2026/27/Recibos/Septiembre 26"

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
        msg = drive_service._describe_error(HttpError(SimpleNamespace(status=404, reason="NF"), b"{}"))
        assert "no encontrada" in msg.lower()

    def test_transient_http_is_labelled_transient(self):
        msg = drive_service._describe_error(HttpError(SimpleNamespace(status=503, reason="x"), b"{}"))
        assert "transitorio" in msg.lower()

    def test_other_http_status(self):
        msg = drive_service._describe_error(HttpError(SimpleNamespace(status=418, reason="teapot"), b"{}"))
        assert "418" in msg

    def test_non_http_error_returns_type_name(self):
        assert drive_service._describe_error(ValueError("boom")) == "ValueError"


class TestCredentialRoute:
    """How the service authenticates to Drive.

    Plain ADC cannot do this job: Cloud Run's metadata server issues
    `cloud-platform`-scoped tokens and that scope does NOT cover
    `https://www.googleapis.com/auth/drive`, so a default credential fails on
    scope alone. The app therefore has the runtime service account impersonate
    ITSELF to mint a short-lived drive-scoped token — which is what lets
    production run with no stored key material, the same call this project made
    when it stripped OAuth refresh tokens out of the session table.
    """

    SA = "fiveaday-run@five-a-day-evolution.iam.gserviceaccount.com"

    def test_impersonation_alone_counts_as_configured(self, settings):
        """No key anywhere, and the feature is still fully configured."""
        settings.GOOGLE_DRIVE_RECEIPTS_FOLDER_ID = "folder-1"
        settings.GOOGLE_DRIVE_IMPERSONATE_SA = self.SA
        settings.GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON = ""
        settings.GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE = ""
        assert DriveReceiptService().is_configured() is True

    def test_a_key_alone_still_counts(self, settings):
        """The fallback must keep working — local runs and anyone who cannot be
        granted the token-creator role depend on it."""
        settings.GOOGLE_DRIVE_RECEIPTS_FOLDER_ID = "folder-1"
        settings.GOOGLE_DRIVE_IMPERSONATE_SA = ""
        with patch.object(DriveReceiptService, "_credential_info", return_value={"type": "service_account"}):
            assert DriveReceiptService().is_configured() is True

    def test_neither_is_not_configured(self, settings):
        settings.GOOGLE_DRIVE_RECEIPTS_FOLDER_ID = "folder-1"
        settings.GOOGLE_DRIVE_IMPERSONATE_SA = ""
        with patch.object(DriveReceiptService, "_credential_info", return_value=None):
            assert DriveReceiptService().is_configured() is False

    def test_a_credential_without_a_folder_is_not_configured(self, settings):
        """Both halves are required: an impersonation target with nowhere to put
        the file is not a working archive."""
        settings.GOOGLE_DRIVE_RECEIPTS_FOLDER_ID = ""
        settings.GOOGLE_DRIVE_IMPERSONATE_SA = self.SA
        assert DriveReceiptService().is_configured() is False

    def test_the_source_token_is_requested_with_cloud_platform(self, settings):
        """The precise trap this design exists around.

        The SOURCE credential is only used to call IAM, so it asks for
        `cloud-platform`. Asking the metadata server for the drive scope here is
        exactly what does not work, and doing so would fail at runtime in a way
        no unit test would otherwise notice.
        """
        settings.GOOGLE_DRIVE_RECEIPTS_FOLDER_ID = "folder-1"
        settings.GOOGLE_DRIVE_IMPERSONATE_SA = self.SA
        fake_source = MagicMock()

        with (
            patch("google.auth.default", return_value=(fake_source, "proj")) as mock_default,
            patch("google.auth.impersonated_credentials.Credentials") as mock_creds,
        ):
            DriveReceiptService()._build_credentials()

        assert mock_default.call_args.kwargs["scopes"] == ["https://www.googleapis.com/auth/cloud-platform"]
        kwargs = mock_creds.call_args.kwargs
        assert kwargs["target_principal"] == self.SA
        # ...while the TARGET carries the drive scope, which is the whole point.
        assert kwargs["target_scopes"] == ["https://www.googleapis.com/auth/drive"]
        assert kwargs["source_credentials"] is fake_source

    def test_impersonation_wins_when_both_are_set(self, settings):
        """Production must not silently prefer stored key material."""
        settings.GOOGLE_DRIVE_RECEIPTS_FOLDER_ID = "folder-1"
        settings.GOOGLE_DRIVE_IMPERSONATE_SA = self.SA

        with (
            patch("google.auth.default", return_value=(MagicMock(), "proj")),
            patch("google.auth.impersonated_credentials.Credentials") as mock_impersonated,
            patch("google.oauth2.service_account.Credentials.from_service_account_info") as mock_key,
            patch.object(DriveReceiptService, "_credential_info", return_value={"type": "service_account"}),
        ):
            DriveReceiptService()._build_credentials()

        assert mock_impersonated.called
        assert not mock_key.called, "a stored key must not be preferred over impersonation"

    def test_the_key_path_is_used_when_no_target_is_set(self, settings):
        settings.GOOGLE_DRIVE_RECEIPTS_FOLDER_ID = "folder-1"
        settings.GOOGLE_DRIVE_IMPERSONATE_SA = ""

        with (
            patch("google.oauth2.service_account.Credentials.from_service_account_info") as mock_key,
            patch.object(DriveReceiptService, "_credential_info", return_value={"type": "service_account"}),
        ):
            DriveReceiptService()._build_credentials()

        assert mock_key.call_args.kwargs["scopes"] == ["https://www.googleapis.com/auth/drive"]

    def test_no_credential_at_all_raises_inside_the_guarded_path(self, settings):
        """`upload_receipt` converts this into an `error` result — it must not
        escape, but it must not be silent either."""
        settings.GOOGLE_DRIVE_RECEIPTS_FOLDER_ID = "folder-1"
        settings.GOOGLE_DRIVE_IMPERSONATE_SA = ""
        with (
            patch.object(DriveReceiptService, "_credential_info", return_value=None),
            pytest.raises(RuntimeError, match="not configured"),
        ):
            DriveReceiptService()._build_credentials()
