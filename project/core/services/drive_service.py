"""Google Drive receipt archive (v1.29.0).

Uploads each completed payment's receipt PDF to the academy's Drive, mirroring
the paper archive, under:

    <base folder>/Curso YYYY/YYYY+1/Recibos/<Mes> YY/<paymentID>_<nombre>_<apellidos>.pdf

The base folder is `settings.GOOGLE_DRIVE_RECEIPTS_FOLDER_ID` and the service
account (reused from the Sheets integration, `drive` scope) must have Editor
access to it. Every level of the path is find-or-created.

**Only PRODUCTION archives receipts (v1.29.5).** Uploading from anywhere else
writes into the academy's real, permanent archive, so a QA seed run or a
developer clicking "marcar cobrado" would file fictional receipts beside the
ones the academy files with its accountant — indistinguishable after the fact.
`drive_uploads_allowed()` is THE gate: production always, the QA VM only while
the `/testing/` toggle is on (`QAConfiguration.drive_uploads_enabled`, off by
default) and then only into a `testing/` subfolder of the month
(`archive_subfolder()`), development and the test suite never. Both the
on-completion task and `backfill_drive_receipts` go through it, because the
command builds a `DriveReceiptService` directly and would otherwise write real
paths from the VM.

**This module is best-effort and NEVER raises.** The Drive archive is a
convenience on top of the real records (the `Payment` row and the emailed
receipt), so a Drive outage, a mis-shared folder or a bad credential must not
break payment completion. Every public method returns a `DriveUploadResult`
describing what happened, and callers log it; nothing here propagates.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from dataclasses import dataclass
from datetime import date

from django.conf import settings

from core.constants import MESES_ES
from core.log_safe import safe_log
from core.models import QAConfiguration
from core.services.google_sheets_service import _load_service_account_info

logger = logging.getLogger(__name__)

# Full `drive` scope, NOT `drive.file`: the academy's Curso/Recibos folders were
# created by hand, and `drive.file` only grants access to files the app itself
# created — it cannot see, let alone write into, a human-made folder even when it
# is shared. `drive` lets the service account use everything shared with it.
_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

_FOLDER_MIME = "application/vnd.google-apps.folder"

#: "not resolved yet", so a cached `None` (genuinely unconfigured) is not
#: mistaken for a cache miss and re-read on every call.
_UNSET = object()

#: How long a single Drive API call may take. googleapiclient's default is
#: `socket.getdefaulttimeout()` — i.e. no timeout at all — and production runs
#: Celery eager, so these calls happen INSIDE the "marcar cobrado" request. A
#: hung connection would park a Gunicorn worker indefinitely: "never raises" is
#: not the same as "never blocks", and an archive that is explicitly best-effort
#: must fail fast rather than hold a request open.
_DRIVE_TIMEOUT_SECONDS = 10

#: The extra folder level QA uploads are quarantined into, inside the month:
#: `<Mes> YY/testing/`. A separate folder rather than a filename prefix because
#: the academy's accountant opens these folders — a fictional receipt has to be
#: somewhere they will never scroll past, not merely named differently.
TESTING_SUBFOLDER = "testing"


def _is_production() -> bool:
    return getattr(settings, "ENVIRONMENT", "") == "production"


def drive_uploads_allowed() -> bool:
    """THE gate on whether this environment may write to the Drive archive.

    Production: always, and without touching the database — a DB blip must never
    be able to switch the real archive off. The QA VM (``IS_TESTING_ENV``): only
    while ``QAConfiguration.drive_uploads_enabled`` is on, which is off by
    default and is the ``/testing/`` dashboard's "Recibos a Drive" toggle.
    Everywhere else — development, Docker, the test suite: never.

    One predicate, read by ``DriveReceiptService.upload_receipt`` (the true
    enforcement point), by ``core.tasks.upload_receipt_to_drive_task`` (so a
    disallowed environment does not even render the PDF) and by
    ``backfill_drive_receipts`` (which builds its own service instance and would
    otherwise walk the whole archive reporting one refusal per payment).

    Fails CLOSED on a database error: the safe direction here is "do not write to
    the academy's permanent archive", and this module never raises.
    """
    if _is_production():
        return True
    if not getattr(settings, "IS_TESTING_ENV", False):
        return False

    try:
        return bool(QAConfiguration.get_config().drive_uploads_enabled)
    except Exception:  # best-effort archive; fail closed
        logger.exception("Could not read the QA Drive-upload toggle; refusing the upload")
        return False


def archive_subfolder() -> str:
    """The month subfolder receipts are filed into: `""` in production,
    ``TESTING_SUBFOLDER`` anywhere else.

    Only ever consulted once `drive_uploads_allowed()` has said yes, so "not
    production" here means "the QA VM with the toggle on". Derived from the same
    `_is_production()` check as the gate, so the two cannot disagree — a QA
    upload that landed in the real month folder would be indistinguishable from a
    genuine receipt.
    """
    return "" if _is_production() else TESTING_SUBFOLDER


@dataclass
class DriveUploadResult:
    """Outcome of an upload attempt — always safe to return; never raised."""

    success: bool
    status: str  # uploaded | skipped_exists | disabled | not_configured | error
    folder_path: str = ""
    file_id: str = ""
    error: str = ""

    def as_dict(self) -> dict:
        # `asdict`, not a hand-written copy of the five fields: a sixth would
        # otherwise vanish from the task result until someone remembered to
        # extend this too.
        return dataclasses.asdict(self)


def curso_folder_name(d: date) -> str:
    """The `Curso YYYY/YYYY+1` folder for a receipt dated `d`.

    The academy rolls receipts onto the NEW course in **August** (a receipt dated
    Aug–Dec of year Y belongs to Curso Y/Y+1; Jan–Jul belongs to Y-1/Y). This is
    deliberately the August boundary the academy files by, which differs from the
    billing academic year — do not "align" them.
    """
    if d.month >= 8:
        start = d.year
    else:
        start = d.year - 1
    return f"Curso {start}/{start + 1}"


def month_folder_name(d: date) -> str:
    """The `<Mes> YY` folder, e.g. `Septiembre 26`.

    The month names come from `core.constants.MESES_ES` (capitalised here) and
    not from a local list. Drive folders are matched BY NAME, so a second list
    that drifted by one accent would silently file receipts into a new,
    differently-spelled folder beside the real one — find-or-create succeeds
    either way and nothing would error.
    """

    return f"{MESES_ES[d.month - 1].capitalize()} {d:%y}"


def _sanitize(part: str) -> str:
    """Make a name safe for a Drive filename: no slashes or control chars,
    collapsed whitespace. Drive itself is permissive, but a `/` in a name is
    confusing and control chars break the API."""
    cleaned = re.sub(r"[\x00-\x1f/\\]+", " ", str(part or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "sin-nombre"


def receipt_filename(payment) -> str:
    """`<paymentID>_<nombre>_<apellidos>.pdf` — keyed on the payment id so the
    upload is idempotent even if the student's name later changes."""
    student = payment.student
    first = _sanitize(getattr(student, "first_name", "") if student else "")
    last = _sanitize(getattr(student, "last_name", "") if student else "")
    return f"{payment.id}_{first}_{last}.pdf".replace(" ", "-")


class DriveReceiptService:
    """Uploads receipt PDFs to the Drive archive. Best-effort, never raises."""

    def __init__(self, base_folder_id: str | None = None):
        self.base_folder_id = (
            base_folder_id if base_folder_id is not None else getattr(settings, "GOOGLE_DRIVE_RECEIPTS_FOLDER_ID", "")
        )
        self._service = None
        self._folder_cache: dict[tuple[str, str], str] = {}
        self._credentials = _UNSET

    def _credential_info(self):
        """The parsed service-account credential, resolved once per instance.

        `_load_service_account_info` re-opens the file (or re-parses the inline
        secret) on every call, and this is asked once per payment in the task's
        guard and again inside `upload_receipt` — so a backfill over the whole
        archive re-parsed the same JSON thousands of times. Sentinel-cached, so
        a genuine "not configured" is remembered too.
        """
        if self._credentials is _UNSET:
            self._credentials = _service_account_info()
        return self._credentials

    def is_configured(self) -> bool:
        """True iff a base folder and service-account credentials are both set."""
        return bool(self.base_folder_id) and self._credential_info() is not None

    # ── Drive client ─────────────────────────────────────────────────────────

    def _get_service(self):
        # Deliberately lazy. googleapiclient / google-auth-oauthlib /
        # google-auth-httplib2 / httplib2 are TRANSITIVE deps (via django-gsheets),
        # not declared in pyproject.toml — at module level a shift in that tree
        # turns a degraded Google feature into an app that cannot boot. It also
        # keeps ~300 ms of Google stack off every cold start for a path most
        # requests never take.
        # Deliberately lazy. googleapiclient / google-auth-oauthlib /
        # google-auth-httplib2 / httplib2 are TRANSITIVE deps (via django-gsheets),
        # not declared in pyproject.toml — at module level a shift in that tree
        # turns a degraded Google feature into an app that cannot boot. It also
        # keeps ~300 ms of Google stack off every cold start for a path most
        # requests never take.
        # Deliberately lazy. googleapiclient / google-auth-oauthlib /
        # google-auth-httplib2 / httplib2 are TRANSITIVE deps (via django-gsheets),
        # not declared in pyproject.toml — at module level a shift in that tree
        # turns a degraded Google feature into an app that cannot boot. It also
        # keeps ~300 ms of Google stack off every cold start for a path most
        # requests never take.
        # Deliberately lazy. googleapiclient / google-auth-oauthlib /
        # google-auth-httplib2 / httplib2 are TRANSITIVE deps (via django-gsheets),
        # not declared in pyproject.toml — at module level a shift in that tree
        # turns a degraded Google feature into an app that cannot boot. It also
        # keeps ~300 ms of Google stack off every cold start for a path most
        # requests never take.
        import google_auth_httplib2
        import httplib2
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        if self._service is not None:
            return self._service
        info = self._credential_info()
        if info is None:
            raise RuntimeError("Google service account credentials are not configured.")
        # Late imports: the google-api-client stack is heavy and only needed when
        # an upload actually runs.

        creds = Credentials.from_service_account_info(info, scopes=_DRIVE_SCOPES)

        # An explicit transport, purely to bound the socket — see
        # `_DRIVE_TIMEOUT_SECONDS`. Without it a stalled Drive connection holds
        # the request open forever.

        http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=_DRIVE_TIMEOUT_SECONDS))
        # cache_discovery=False: the default file cache warns under non-writable
        # homes (Cloud Run) and is useless for a short-lived job.
        self._service = build("drive", "v3", http=http, cache_discovery=False)
        return self._service

    # ── Folder resolution ────────────────────────────────────────────────────

    def _list_folders(self, service, name: str, parent_id: str) -> list[dict]:
        """Every folder called `name` under `parent_id`, oldest first."""
        safe_name = name.replace("\\", "\\\\").replace("'", "\\'")
        query = f"name = '{safe_name}' and mimeType = '{_FOLDER_MIME}' and '{parent_id}' in parents and trashed = false"
        response = (
            service.files()
            .list(
                q=query,
                fields="files(id, name, createdTime)",
                # Deterministic, and it is what makes a raced duplicate harmless:
                # every instance picks the SAME winner rather than whichever the
                # API happened to return first.
                orderBy="createdTime",
                pageSize=10,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        return response.get("files", [])

    def _find_or_create_folder(self, service, name: str, parent_id: str) -> str:
        """Return the id of the folder `name` under `parent_id`, creating it if it
        does not exist. Cached per (parent, name) for the life of the service.

        Find-then-create is not atomic and Drive permits duplicate names, so two
        payments completing at the start of a month on two Cloud Run instances
        could each create "Septiembre 26" and then file receipts into different
        folders — splitting the archive and defeating the per-payment
        idempotency check, with nothing erroring. It cannot be locked, so it is
        made CONVERGENT instead: after creating, re-list and keep the oldest
        match. A loser's folder is left empty rather than used, so every instance
        ends up writing to the same one.
        """
        cache_key = (parent_id, name)
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        files = self._list_folders(service, name, parent_id)
        if files:
            folder_id = files[0]["id"]
        else:
            created = (
                service.files()
                .create(
                    body={"name": name, "mimeType": _FOLDER_MIME, "parents": [parent_id]},
                    fields="id",
                    supportsAllDrives=True,
                )
                .execute()
            )
            folder_id = created["id"]
            logger.info("Drive: created folder %s under %s", safe_log(name), safe_log(parent_id))

            # Collapse a concurrent create: whoever's folder is oldest wins for
            # everybody. Only on the create path, so the steady state still costs
            # one list per folder per process.
            raced = self._list_folders(service, name, parent_id)
            if raced and raced[0]["id"] != folder_id:
                logger.info("Drive: folder %s was created concurrently; using the earlier one", safe_log(name))
                folder_id = raced[0]["id"]

        self._folder_cache[cache_key] = folder_id
        return folder_id

    def _existing_receipt(self, service, folder_id: str, payment_id: int) -> str | None:
        """Return the id of an already-uploaded receipt for this payment in the
        destination folder, or None. Matches on the `<paymentID>_` prefix so a
        renamed student does not produce a duplicate.

        Scoped to the folder the upload is going into, so the QA sandbox and the
        real archive each stay idempotent on their own."""
        query = f"'{folder_id}' in parents and trashed = false and name contains '{payment_id}_'"
        response = (
            service.files()
            .list(
                q=query,
                fields="files(id, name)",
                pageSize=100,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        for f in response.get("files", []):
            # `contains` is a substring match, so 5_ also matches 15_; check the
            # real leading id token.
            if f["name"].split("_", 1)[0] == str(payment_id):
                return f["id"]
        return None

    # ── Public API ───────────────────────────────────────────────────────────

    def upload_receipt(self, payment, pdf_bytes: bytes) -> DriveUploadResult:
        """Upload one receipt PDF, creating the Curso/Recibos/<Mes> path as needed.

        Refuses outright (`status="disabled"`) unless `drive_uploads_allowed()`;
        on the QA VM with the toggle on it files into the `testing/` sandbox
        inside the month folder instead of beside the real receipts.

        Idempotent (skips if a receipt for this payment id is already in the month
        folder) and NEVER raises — returns a result describing the outcome. The
        distinct failure statuses matter operationally, so each is logged with a
        message aimed at the actual cause (sharing vs. wrong id vs. transient).
        """
        # Deliberately lazy. googleapiclient / google-auth-oauthlib /
        # google-auth-httplib2 / httplib2 are TRANSITIVE deps (via django-gsheets),
        # not declared in pyproject.toml — at module level a shift in that tree
        # turns a degraded Google feature into an app that cannot boot. It also
        # keeps ~300 ms of Google stack off every cold start for a path most
        # requests never take.
        from googleapiclient.http import MediaInMemoryUpload

        # `Payment.receipt_date` — the same property that picks the YEAR of the
        # receipt NUMBER, so a receipt cannot be numbered for one year and filed
        # under another.
        d = payment.receipt_date
        # The path levels, in order, so the reported `folder_path` and the folders
        # actually created come from ONE list — a QA upload reported against the
        # real path would be the whole point of the sandbox lost.
        levels = [curso_folder_name(d), "Recibos", month_folder_name(d)]
        sandbox = archive_subfolder()
        if sandbox:
            levels.append(sandbox)
        folder_path = "/".join(levels)

        # The environment gate FIRST: outside production this must not touch the
        # credentials, let alone Drive. See `drive_uploads_allowed`.
        if not drive_uploads_allowed():
            return DriveUploadResult(success=False, status="disabled", folder_path=folder_path)

        if not self.is_configured():
            return DriveUploadResult(success=False, status="not_configured", folder_path=folder_path)

        try:
            service = self._get_service()
            dest_id = self.base_folder_id
            for level in levels:
                dest_id = self._find_or_create_folder(service, level, dest_id)

            existing = self._existing_receipt(service, dest_id, payment.id)
            if existing:
                return DriveUploadResult(
                    success=True, status="skipped_exists", folder_path=folder_path, file_id=existing
                )

            media = MediaInMemoryUpload(pdf_bytes, mimetype="application/pdf", resumable=False)
            created = (
                service.files()
                .create(
                    body={"name": receipt_filename(payment), "parents": [dest_id]},
                    media_body=media,
                    fields="id",
                    supportsAllDrives=True,
                )
                .execute()
            )
            logger.info("Drive: uploaded receipt for payment %s to %s", payment.id, safe_log(folder_path))
            return DriveUploadResult(success=True, status="uploaded", folder_path=folder_path, file_id=created["id"])
        except Exception as exc:  # noqa: BLE001 — best-effort archive, never propagate
            return DriveUploadResult(
                success=False,
                status="error",
                folder_path=folder_path,
                error=_describe_error(exc),
            )


def _describe_error(exc: Exception) -> str:
    """A short, cause-oriented message for the log — no raw exception text to the
    client, and the common Drive failures named so an operator knows what to fix."""
    # NOT a top-level import: here the import IS the condition. The `except`
    # binds `HttpError = ()` so the isinstance() below is simply False when
    # googleapiclient is unavailable — this module's contract is that a Drive
    # problem never raises into the payment flow, and an error FORMATTER is the
    # last place that should be able to fail. Hoisting it would also leave an
    # empty `try:` block. The module's other googleapiclient imports are at the
    # top; only this fallback pair has to stay.
    try:
        from googleapiclient.errors import HttpError
    except Exception:  # noqa: BLE001
        HttpError = ()  # type: ignore[assignment]

    if HttpError and isinstance(exc, HttpError):
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status == 403:
            msg = (
                "permiso denegado — comparte la carpeta base de Drive con la cuenta "
                "de servicio como Editor (GOOGLE_DRIVE_RECEIPTS_FOLDER_ID)"
            )
        elif status == 404:
            msg = "carpeta base no encontrada — revisa GOOGLE_DRIVE_RECEIPTS_FOLDER_ID y que esté compartida"
        elif status in (429, 500, 502, 503):
            msg = f"error transitorio de Drive (HTTP {status})"
        else:
            msg = f"error de Drive (HTTP {status})"
        logger.warning("Drive receipt upload failed: %s", msg)
        return msg

    msg = f"{type(exc).__name__}"
    logger.exception("Drive receipt upload failed unexpectedly")
    return msg


def _service_account_info():
    """Reuse the Sheets integration's credential loader — same service account,
    one place that knows how to read the JSON/file setting."""

    return _load_service_account_info()


# Module-level singleton, mirroring `google_sheets_service.get_service()`.
_service_singleton: DriveReceiptService | None = None


def get_service() -> DriveReceiptService:
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = DriveReceiptService()
    return _service_singleton
