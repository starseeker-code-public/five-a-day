"""Shared machinery for the end-to-end journeys: reporting, cleanup, retries.

IMPORT ORDER MATTERS. This module imports Django models at top level, so it may
only be imported *after* `django.setup()`. `run.py` is the single entry point and
does that bootstrap before it imports anything else, which is why a journey is an
importable module rather than a script with its own `__main__`.

Three concerns live here, and each one exists because it bit a real run:

* **Reporting** — a `Report` per journey rather than a module-level failure list,
  so running four journeys in one process cannot attribute one's failure to
  another.
* **Cleanup** — `purge_marked_rows()` runs at the START of a journey as well as
  the end. A run that dies between creating the parent and deleting it leaves a
  row holding `Parent.dni`, which is the model's only unique column, so the NEXT
  run failed on an IntegrityError that had nothing to do with the code under
  test. Cleaning up on the way in is what makes a journey re-runnable after a
  crash, a Ctrl-C or a lost network.
* **Flake suppression that stays honest** — `with_retry` and `poll_for_file`
  absorb the two failures that are infrastructure rather than regression (see
  their docstrings). Everything else is reported as a failure. A gate that goes
  red for reasons the committer cannot fix is a gate that gets `--no-verify`d,
  and `--no-verify` also skips the coverage gate.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from django.conf import settings

from billing.models import Enrollment, Payment
from core.models import GoogleDriveCredential
from core.services import drive_service
from core.services.drive_service import DriveReceiptService
from students.models import Parent, Student, StudentParent

#: Every row a journey creates is prefixed with this. It is Spanish and shouty on
#: purpose: if a run dies half way, what it left behind is obvious to whoever
#: opens the students list next, and trivially greppable in the database.
MARK = "E2E_BORRAR"

#: Journeys upload into `<receipts folder>/app_testing/...`, never the academy's
#: real `Curso YYYY/YY/Recibos/...` tree. An earlier manual test filed two
#: fictional receipts beside the genuine ones and nothing in a receipt PDF says
#: which environment produced it — the accountant opens those folders.
SANDBOX_FOLDER = "app_testing"

#: Exit codes. The runner aggregates: any FAILED wins over any UNVERIFIED.
PASSED = 0
FAILED = 1
UNVERIFIED = 2


class Unverified(Exception):
    """Raise to end a journey with "could not run", as distinct from "failed".

    The distinction is the whole value of the gate. A missing Drive credential,
    a container with no DNS or a database that went away say nothing about the
    code being committed; a failed check does. Collapsing the two teaches you to
    ignore both.
    """


class Report:
    """Per-journey phase printing and check tallying."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.failures: list[str] = []

    def phase(self, number: int, title: str) -> None:
        rule = "=" * 72
        print(f"\n{rule}\n  PHASE {number} — {title}\n{rule}")

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        suffix = f" — {detail}" if detail else ""
        print(f"   [{'PASS' if ok else 'FAIL'}] {label}{suffix}")
        if not ok:
            self.failures.append(label)
        return ok

    def note(self, message: str) -> None:
        """Progress that is not a check — retries, waits, cleanup counts."""
        print(f"   ...  {message}")

    @property
    def passed(self) -> bool:
        return not self.failures


# ── Flake suppression ───────────────────────────────────────────────────────


def _transport_errors() -> tuple[type[BaseException], ...]:
    """The failures that mean "could not reach Google", not "the code regressed".

    Measured on a dev box: roughly one run in three died inside the credential
    refresh with `TransportError: Unable to find the server at
    oauth2.googleapis.com`, a container DNS hiccup. `OSError` covers the socket
    and DNS failures underneath it.
    """
    errors: list[type[BaseException]] = [OSError]
    try:
        from google.auth.exceptions import TransportError
    except ImportError:  # the google stack is an optional dependency here
        pass
    else:
        errors.append(TransportError)
    return tuple(errors)


TRANSPORT_ERRORS: tuple[type[BaseException], ...] = _transport_errors()


def with_retry[T](report: Report, label: str, call: Callable[[], T], attempts: int = 3, pause: float = 4.0) -> T:
    """Run a network call, retrying ONLY the network-shaped failures."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except TRANSPORT_ERRORS as exc:
            if attempt == attempts:
                raise
            report.note(
                f"{label}: cannot reach Google ({type(exc).__name__}). Retry {attempt}/{attempts - 1} in {pause:.0f}s"
            )
            time.sleep(pause)
    raise AssertionError("unreachable")


def poll_for_file(
    report: Report,
    api: Any,
    name_prefix: str,
    attempts: int = 6,
    pause: float = 4.0,
) -> list[dict]:
    """Look for an uploaded file until Drive admits it exists.

    Drive's `name contains` query is served from a SEARCH INDEX, and that index
    is eventually consistent: a file really uploaded a second ago is routinely
    absent from the next query for several seconds. A single lookup therefore
    reported "not found" on a run where the upload had in fact succeeded — a
    false red, which is the expensive kind. Polling turns the lag into a wait and
    still fails honestly when nothing was ever uploaded.
    """
    for attempt in range(1, attempts + 1):
        files = with_retry(
            report,
            "Drive lookup",
            lambda: (
                api.files()
                .list(
                    q=f"name contains '{name_prefix}' and trashed = false",
                    fields="files(id,name,size,parents)",
                )
                .execute()
                .get("files", [])
            ),
        )
        if files:
            return files
        if attempt < attempts:
            report.note(f"not in the Drive index yet. Retry {attempt}/{attempts - 1} in {pause:.0f}s")
            time.sleep(pause)
    return []


# ── Database cleanup ────────────────────────────────────────────────────────


def purge_marked_rows(report: Report | None = None) -> int:
    """Delete every row this suite has ever created, in FK order.

    Called BEFORE a journey as well as after it. `Parent.dni` is the only unique
    column on that model, so a crashed run that left a parent behind made the
    next run fail on an IntegrityError — a red gate reporting a defect in the
    previous run's cleanup, not in the code being committed.

    FK order is forced because the app uses PROTECT deliberately: payments
    protect their student, enrollments protect theirs. A `Student.delete()` first
    would raise rather than cascade, which is the schema working as designed.
    """
    deleted = 0
    deleted += Payment.objects.filter(student__first_name=MARK).delete()[0]
    deleted += Enrollment.objects.filter(student__first_name=MARK).delete()[0]
    deleted += StudentParent.objects.filter(student__first_name=MARK).delete()[0]
    deleted += Student.objects.filter(first_name=MARK).delete()[0]
    deleted += Parent.objects.filter(first_name=MARK).delete()[0]

    if report is not None and deleted:
        report.note(f"purged {deleted} row(s) left by an earlier run")
    return deleted


def marked_rows_remaining() -> int:
    """How many people-shaped rows the suite still owns. Must be 0 after a run."""
    return Student.objects.filter(first_name=MARK).count() + Parent.objects.filter(first_name=MARK).count()


# ── Google Drive sandbox ────────────────────────────────────────────────────


def open_drive_sandbox(report: Report) -> tuple[Any, str]:
    """Connect to Drive and point every upload at the `app_testing` sandbox.

    Returns `(api, sandbox_folder_id)`. Raises `Unverified` when Drive is not
    connected or unreachable — neither says anything about the code under test.

    It MUTATES `settings.GOOGLE_DRIVE_RECEIPTS_FOLDER_ID` and clears the service
    singleton, which is what keeps the app's own uploader (which reads the
    setting) inside the sandbox without the journey having to reimplement it.
    """
    credential = GoogleDriveCredential.objects.filter(pk=1).first()
    if not (credential and credential.is_connected):
        raise Unverified("Google Drive is not connected — connect an account at /management/ first")

    base_folder = settings.GOOGLE_DRIVE_RECEIPTS_FOLDER_ID
    if not base_folder:
        raise Unverified("GOOGLE_DRIVE_RECEIPTS_FOLDER_ID is not set")

    report.check("Google Drive is connected", True, credential.account_email)

    try:
        api = with_retry(report, "Drive handshake", lambda: DriveReceiptService()._get_service())
        sandbox_id = with_retry(
            report,
            "sandbox folder",
            lambda: DriveReceiptService()._find_or_create_folder(api, SANDBOX_FOLDER, base_folder),
        )
    except TRANSPORT_ERRORS as exc:
        raise Unverified(f"Google is unreachable from this container — {type(exc).__name__}: {exc}") from exc

    report.check(f"sandbox folder '{SANDBOX_FOLDER}' ready", bool(sandbox_id), sandbox_id)

    settings.GOOGLE_DRIVE_RECEIPTS_FOLDER_ID = sandbox_id
    drive_service._service_singleton = None
    return api, sandbox_id


def drive_delete(report: Report, api: Any, file_id: str, label: str) -> None:
    """Best-effort delete. Cleanup must never turn a passing run red."""
    try:
        with_retry(report, f"delete {label}", lambda: api.files().delete(fileId=file_id).execute())
        report.note(f"deleted {label} ({file_id})")
    except Exception as exc:  # noqa: BLE001 — cleanup is best-effort by contract
        report.note(f"could not delete {label} ({file_id}): {type(exc).__name__}: {exc}")
