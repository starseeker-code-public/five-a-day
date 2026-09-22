"""Unit tests for the Google Drive receipt archive (v1.29.0).

The Drive client is mocked — these assert the folder-path logic, idempotency,
and the guarantee that the service NEVER raises whatever Drive does.

Since v1.29.5 the archive is production-only, so every test that expects an
upload has to say it is production (the `_production` autouse fixture below).
`override_settings` cannot decorate a plain pytest class — only a
`SimpleTestCase` — hence a fixture rather than a class decorator.
"""

import socket
import ssl
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

    @pytest.mark.parametrize(
        "exc",
        [
            BrokenPipeError(32, "Broken pipe"),
            ConnectionResetError(104, "Connection reset by peer"),
            TimeoutError("timed out"),
            ssl.SSLError("handshake failed"),
            socket.gaierror("Name or service not known"),
        ],
    )
    def test_a_dropped_connection_is_named_as_one(self, exc):
        """These used to fall through to the `logger.exception` branch and be
        described by their bare type name, i.e. reported like a code defect."""
        msg = drive_service._describe_error(exc)
        assert "conexi" in msg.lower()
        assert type(exc).__name__ in msg

    def test_the_message_says_which_receipt(self):
        what = "payment 633 (633_Ana_Ruiz.pdf) -> Curso 2026/27/Recibos/Septiembre 26"
        with patch.object(drive_service, "logger") as log:
            drive_service._describe_error(BrokenPipeError(32, "Broken pipe"), what=what)
        logged = " ".join(str(a) for a in log.warning.call_args.args)
        assert "633_Ana_Ruiz.pdf" in logged
        assert "Septiembre 26" in logged

    def test_an_unexpected_error_still_says_which_receipt(self):
        """The `logger.exception` branch is the one an operator can least afford
        to be told about in the abstract — it is also the only one that mails
        the admins."""
        with patch.object(drive_service, "logger") as log:
            drive_service._describe_error(ValueError("boom"), what="payment 633 (633_Ana_Ruiz.pdf) -> Sept")
        assert "633_Ana_Ruiz.pdf" in " ".join(str(a) for a in log.exception.call_args.args)

    def test_a_dropped_connection_does_not_mail_the_admins(self):
        """WARNING, not `logger.exception`. The production log config wires
        ERROR to `mail_admins`, so the old behaviour paged the academy with a
        30-frame httplib2 traceback every time Google closed an idle socket."""
        with patch.object(drive_service, "logger") as log:
            drive_service._describe_error(BrokenPipeError(32, "Broken pipe"), what="x")
        log.exception.assert_not_called()
        log.warning.assert_called_once()


class TestPoisonedConnection:
    """httplib2 keeps one connection per host on the `Http` object and evicts it
    ONLY for `socket.timeout`. Every other transport failure — a broken pipe
    above all — leaves the dead socket cached with `conn.sock` still set, so
    nothing reconnects.

    That matters because this service is held for a long time: `get_service()`
    is a module singleton living for the life of the Cloud Run instance, and
    `backfill_drive_receipts` holds ONE instance for the whole archive. So a
    single dropped socket did not cost one receipt, it cost every receipt after
    it. Same shape as `EmailService.send_bulk_emails`.
    """

    @pytest.fixture(autouse=True)
    def _production(self, settings):
        settings.ENVIRONMENT = "production"

    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_the_client_is_dropped_and_closed(self, _info):
        svc = DriveReceiptService(base_folder_id="BASE")
        client = MagicMock()
        svc._service = client

        svc._drop_poisoned_client()

        assert svc._service is None
        client._http.close.assert_called_once()

    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_a_client_that_cannot_be_closed_is_still_dropped(self, _info):
        """Closing an already-dead socket must not raise out of a best-effort
        archive — and must not leave the poisoned client behind either."""
        svc = DriveReceiptService(base_folder_id="BASE")
        client = MagicMock()
        client._http.close.side_effect = OSError("already closed")
        svc._service = client

        svc._drop_poisoned_client()

        assert svc._service is None

    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_a_broken_pipe_is_retried_on_a_fresh_client(self, _info):
        """The first attempt dies on the stale socket; the second gets a new
        client and files the receipt. Before this the receipt was simply lost."""
        svc = DriveReceiptService(base_folder_id="BASE")

        dead = MagicMock()
        dead.files.return_value.list.return_value.execute.side_effect = BrokenPipeError(32, "Broken pipe")

        fresh = MagicMock()
        fresh.files.return_value.list.return_value.execute.side_effect = [
            {"files": [{"id": "C"}]},
            {"files": [{"id": "R"}]},
            {"files": [{"id": "M"}]},
            {"files": []},
        ]
        fresh.files.return_value.create.return_value.execute.return_value = {"id": "FILE"}

        with patch.object(svc, "_get_service", side_effect=[dead, fresh]) as get:
            result = svc.upload_receipt(_payment(), b"%PDF")

        assert result.status == "uploaded"
        assert result.file_id == "FILE"
        # Two clients, not one reused: the point of the retry is the reconnect.
        assert get.call_count == 2

    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_the_next_payment_does_not_inherit_the_poisoned_client(self, _info):
        """THE regression. The service is a long-lived singleton, so leaving the
        dead client cached turned one dropped socket into a guaranteed failure
        for every later receipt on that instance."""
        svc = DriveReceiptService(base_folder_id="BASE")
        svc._service = "POISONED"

        with patch.object(svc, "_get_service", side_effect=BrokenPipeError(32, "Broken pipe")):
            result = svc.upload_receipt(_payment(), b"%PDF")

        assert result.status == "error"
        assert svc._service is None

    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_two_dropped_sockets_running_give_up_without_raising(self, _info):
        svc = DriveReceiptService(base_folder_id="BASE")
        with patch.object(svc, "_get_service", side_effect=BrokenPipeError(32, "Broken pipe")) as get:
            result = svc.upload_receipt(_payment(), b"%PDF")

        assert result.success is False
        assert result.status == "error"
        assert "conexi" in result.error.lower()
        # Exactly two attempts — the retry is bounded, because production runs
        # Celery eager and this sits inside the "marcar cobrado" request.
        assert get.call_count == 2

    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_a_non_transport_failure_is_not_retried(self, _info):
        """A 403 is a sharing problem: retrying it wastes a round trip inside a
        user request and cannot possibly succeed."""
        svc = DriveReceiptService(base_folder_id="BASE")
        fake = MagicMock()
        fake.files.return_value.list.return_value.execute.side_effect = HttpError(
            SimpleNamespace(status=403, reason="Forbidden"), b"{}"
        )
        with patch.object(svc, "_get_service", return_value=fake) as get:
            result = svc.upload_receipt(_payment(), b"%PDF")

        assert result.status == "error"
        assert get.call_count == 1

    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_a_successful_upload_keeps_its_client(self, _info):
        """The drop is for poisoned clients only — dropping one per upload would
        pay a TLS handshake for every receipt."""
        svc = DriveReceiptService(base_folder_id="BASE")
        fake = MagicMock()
        fake.files.return_value.list.return_value.execute.side_effect = [
            {"files": [{"id": "C"}]},
            {"files": [{"id": "R"}]},
            {"files": [{"id": "M"}]},
            {"files": []},
        ]
        fake.files.return_value.create.return_value.execute.return_value = {"id": "FILE"}
        # Seeded rather than patched, so the real cache is what is under test:
        # `_get_service` returns it and nothing should clear it.
        svc._service = fake

        assert svc.upload_receipt(_payment(), b"%PDF").status == "uploaded"
        assert svc._service is fake


class TestFailureNamesTheReceipt:
    """What the log said before: "Drive receipt upload failed unexpectedly",
    then thirty frames of httplib2. Which payment, which family and which month
    to repair were all absent — from the only record there was."""

    @pytest.fixture(autouse=True)
    def _production(self, settings):
        settings.ENVIRONMENT = "production"

    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_the_result_carries_the_file_name_on_every_outcome(self, _info):
        svc = DriveReceiptService(base_folder_id="BASE")
        fake = MagicMock()
        fake.files.return_value.list.return_value.execute.side_effect = HttpError(
            SimpleNamespace(status=403, reason="Forbidden"), b"{}"
        )
        with patch.object(svc, "_get_service", return_value=fake):
            result = svc.upload_receipt(_payment(pid=633, first="Ana", last="Ruiz"), b"%PDF")

        assert result.file_name == "633_Ana_Ruiz.pdf"
        assert result.folder_path == "Curso 2026/27/Recibos/Septiembre 26"
        # …and it survives into the Celery result dict, which is what the QA
        # dashboard and `backfill_drive_receipts` read.
        assert result.as_dict()["file_name"] == "633_Ana_Ruiz.pdf"

    def test_a_refused_environment_still_names_the_file(self, settings):
        """`disabled` and `not_configured` name it too — they are the two
        outcomes somebody reads while asking why nothing is being archived."""
        settings.ENVIRONMENT = "development"
        result = DriveReceiptService(base_folder_id="BASE").upload_receipt(_payment(), b"%PDF")
        assert result.status == "disabled"
        assert result.file_name == "633_Ana_Ruiz.pdf"

    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_the_retry_warning_names_the_receipt(self, _info):
        svc = DriveReceiptService(base_folder_id="BASE")
        with (
            patch.object(svc, "_get_service", side_effect=BrokenPipeError(32, "Broken pipe")),
            patch.object(drive_service, "logger") as log,
        ):
            svc.upload_receipt(_payment(pid=633), b"%PDF")

        logged = " ".join(str(a) for call in log.warning.call_args_list for a in call.args)
        assert "633_Ana_Ruiz.pdf" in logged
        assert "Curso 2026/27/Recibos/Septiembre 26" in logged
        assert "BrokenPipeError" in logged

    @patch.object(drive_service, "_service_account_info", return_value={"type": "service_account"})
    def test_a_name_with_a_newline_cannot_forge_a_log_record(self, _info):
        """The file name reaches the log, and it is built from a student name an
        admin typed. `_sanitize` is what makes it safe — this fails if the
        filename ever stops being sanitised."""
        svc = DriveReceiptService(base_folder_id="BASE")
        payment = _payment(first="Ana\nERROR fake login", last="Ruiz")
        with (
            patch.object(svc, "_get_service", side_effect=BrokenPipeError(32, "Broken pipe")),
            patch.object(drive_service, "logger") as log,
        ):
            result = svc.upload_receipt(payment, b"%PDF")

        logged = " ".join(str(a) for call in log.warning.call_args_list for a in call.args)
        assert "\n" not in logged
        assert "\n" not in result.file_name


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
