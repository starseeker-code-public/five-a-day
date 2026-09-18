"""END-TO-END: a family is enrolled, billed, paid over HTTP, emailed and archived.

Runs the REAL code paths — the enrollment service, the payment generator, the
`quick-complete` endpoint the payments list actually posts to, the completion
dispatch, the receipt PDF and a genuine upload to Google Drive. Very little is
faked, and what is faked is named in `run.py`'s module docstring.

Phase 3 goes through the HTTP endpoint rather than calling the service, because
the seams between the view, the model guard and the on-commit dispatch are where
this app's completion bugs have actually lived: re-completion rewriting a
historical `payment_date`, a receipt emailed for a transaction that rolled back,
a status transition that skipped `assert_completable`. Calling the service
directly would test the half that has never broken.

Run it with `make e2e ARGS="--only payment"`. See `run.py` for the contract, the
safety refusal and the environment substitutions.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date

from django.core import mail
from django.test import Client
from django.urls import reverse

from billing.models import Payment
from billing.services.enrollment_service import EnrollmentService
from billing.services.payment_service import PaymentService
from core.tasks import upload_receipt_to_drive_task
from students.models import Parent, Student, StudentParent
from tests.e2e._harness import (
    MARK,
    SANDBOX_FOLDER,
    Report,
    Unverified,
    drive_delete,
    marked_rows_remaining,
    open_drive_sandbox,
    poll_for_file,
    purge_marked_rows,
)

NAME = "payment"
SUMMARY = "enrol a family, bill them, collect over HTTP, email the receipt, archive it to Drive"

#: `YYYY-NNN`, continuing the academy's paper books. Asserted as a SHAPE, never
#: as a value: the counter is real and shared with every other receipt in the dev
#: database, so a literal would be a date bomb the first time anyone else runs.
RECEIPT_NUMBER = re.compile(r"\d{4}-\d{3,}")


def _authenticated_client() -> Client:
    """The same session shape `SimpleAuthMiddleware` accepts in the test suite."""
    client = Client()
    session = client.session
    session["is_authenticated"] = True
    session["username"] = "e2e"
    session.save()
    return client


def _rearchive(report: Report, api: object, payment_id: int) -> list[dict]:
    """Run the archive task again, explicitly, and read the status it returns.

    The archive is best-effort BY DESIGN: `upload_receipt_to_drive_task` catches
    everything and returns a status dict, so when the upload dispatched during
    collection times out (a 10-second read timeout on a 3 KB PDF is entirely a
    network event) it leaves no file, no exception and nothing the journey can
    see except an absence. Reported as-is that is a FAILED check, which is a lie:
    the upload path is fine and the network was slow.

    So when the file is missing, ask the task directly. Its status is the only
    thing that can tell the two apart:

    * `uploaded` / `skipped_exists` — the path works; poll again for the file.
    * `error` — transport. Retry; only if every attempt errors is the run
      UNVERIFIED, because a Drive that refuses three uploads in a row says
      nothing about the commit.
    * anything else (`disabled`, `not_configured`) — the journey's own setup is
      wrong, which is also not a regression.
    """
    attempts = 3
    for attempt in range(1, attempts + 1):
        report.note(f"no file yet — re-running the archive task ({attempt}/{attempts})")
        result = upload_receipt_to_drive_task.apply(args=[payment_id]).get()
        status = result.get("status", "")
        if status in ("uploaded", "skipped_exists"):
            return poll_for_file(report, api, f"{payment_id}_", attempts=3)
        if status != "error":
            raise Unverified(f"the Drive archive is not usable from here: status={status!r} {result.get('error', '')}")
        report.note(f"Drive returned an error: {result.get('error') or 'unknown'}")
        time.sleep(4)

    raise Unverified(f"Drive failed the upload {attempts} times in a row — network, not a regression")


def run(report: Report, *, keep: bool = False) -> None:
    # ── PHASE 0 ─────────────────────────────────────────────────────────────
    report.phase(0, "Setup")
    purge_marked_rows(report)
    api, sandbox_id = open_drive_sandbox(report)
    client = _authenticated_client()

    drive_file_id = ""

    try:
        # ── PHASE 1 ─────────────────────────────────────────────────────────
        report.phase(1, "Create the student and the parent")

        parent = Parent.objects.create(
            first_name=MARK,
            last_name="PADRE",
            dni=f"{MARK}-DNI",
            email=f"{MARK.lower()}@example.invalid",
            phone="600000000",
        )
        student = Student.objects.create(
            first_name=MARK,
            last_name="ALUMNO",
            birth_date=date(2015, 5, 5),
            active=True,
        )
        StudentParent.objects.create(student=student, parent=parent)

        report.check("parent created", Parent.objects.filter(pk=parent.pk).exists(), f"id={parent.pk}")
        report.check("student created", Student.objects.filter(pk=student.pk).exists(), f"id={student.pk}")
        report.check("link created", StudentParent.objects.filter(student=student, parent=parent).exists())

        # ── PHASE 2 ─────────────────────────────────────────────────────────
        report.phase(2, "Enrollment auto-creates the payments")

        enrollment = EnrollmentService.create_enrollment(
            student,
            {
                "enrollment_plan": "monthly_full",
                "has_language_cheque": False,
                "is_sibling_discount": False,
                "is_special": False,
                "manual_amount": None,
                "start_date": date.today(),
            },
            is_adult=False,
        )
        PaymentService.schedule_academic_year_payments(enrollment, parent)

        payments = Payment.objects.filter(student=student).order_by("due_date")
        report.check("enrollment created", enrollment.pk is not None, f"id={enrollment.pk}")
        report.check("at least one payment generated", payments.exists(), f"{payments.count()} payment(s)")

        payment = payments.filter(payment_status="pending").first()
        if payment is None:
            report.check("a PENDING payment exists", False, "nothing to collect — the rest cannot run")
            return
        report.check("a PENDING payment exists", True, f"id={payment.pk}")
        report.check("amount is positive", payment.amount > 0, f"{payment.amount} EUR")
        report.check(
            "no receipt number before collection",
            payment.receipt_number == "",
            "correct: only completed rows continue the fiscal sequence",
        )

        # ── PHASE 3 ─────────────────────────────────────────────────────────
        report.phase(3, "The payment is collected through the HTTP endpoint")

        # `mail.outbox` is created by Django's TEST RUNNER, not by the locmem
        # backend itself, so outside pytest it has to be primed by hand.
        mail.outbox = []
        url = reverse("quick_complete_payment", args=[payment.pk])
        response = client.post(url, data=json.dumps({"payment_method": "transfer"}), content_type="application/json")

        report.check("endpoint answered 200", response.status_code == 200, f"HTTP {response.status_code}")
        body = response.json() if response.status_code == 200 else {}
        report.check("endpoint reported success", body.get("success") is True, json.dumps(body)[:120])

        payment.refresh_from_db()
        report.check("status is completed", payment.payment_status == "completed", payment.payment_status)
        report.check("payment_date stamped", payment.payment_date is not None, str(payment.payment_date))
        report.check(
            "receipt number issued",
            bool(RECEIPT_NUMBER.fullmatch(payment.receipt_number or "")),
            payment.receipt_number or "none",
        )

        # ── PHASE 4 ─────────────────────────────────────────────────────────
        report.phase(4, "The receipt email")

        outbox = list(getattr(mail, "outbox", []))
        report.check("exactly one email produced", len(outbox) == 1, f"{len(outbox)} in outbox")
        if outbox:
            message = outbox[0]
            report.check("addressed to the parent", parent.email in message.to, ", ".join(message.to))
            report.check("subject is non-empty", bool(message.subject), message.subject)
            pdfs = [a for a in message.attachments if str(a[0]).lower().endswith(".pdf")]
            report.check("a PDF receipt is attached", bool(pdfs), pdfs[0][0] if pdfs else "none")

        # ── PHASE 5 ─────────────────────────────────────────────────────────
        report.phase(5, "Re-collecting the same payment changes nothing")

        # The expensive regression this app has actually shipped: re-completing
        # rewrote `payment_date` to today, moving banked money into a different
        # month in every income report, and emailed the family a second receipt.
        first_payment_date = payment.payment_date
        first_receipt_number = payment.receipt_number
        mail.outbox = []
        again = client.post(url, data=json.dumps({"payment_method": "cash"}), content_type="application/json")
        payment.refresh_from_db()

        report.check("second attempt is accepted, not an error", again.status_code == 200, f"HTTP {again.status_code}")
        report.check("reported as already completed", again.json().get("already_completed") is True)
        report.check("payment_date unchanged", payment.payment_date == first_payment_date, str(payment.payment_date))
        report.check("receipt number unchanged", payment.receipt_number == first_receipt_number, payment.receipt_number)
        report.check(
            "no second receipt emailed", len(getattr(mail, "outbox", [])) == 0, f"{len(mail.outbox)} in outbox"
        )

        # ── PHASE 6 ─────────────────────────────────────────────────────────
        report.phase(6, "The receipt PDF reached Google Drive")

        found = poll_for_file(report, api, f"{payment.pk}_", attempts=3)
        if not found:
            found = _rearchive(report, api, payment.pk)
        report.check("file present in Drive", bool(found), found[0]["name"] if found else "not found after polling")

        if found:
            drive_file_id = found[0]["id"]
            report.check("file is not empty", int(found[0].get("size", 0)) > 0, f"{found[0].get('size')} bytes")
            report.check(
                "named with the payment id, which is the idempotency key",
                found[0]["name"].startswith(f"{payment.pk}_"),
                found[0]["name"],
            )
            # It must be inside the sandbox tree, not the academy's real archive.
            trail: list[str] = []
            node = found[0]["parents"][0]
            for _ in range(5):
                meta = api.files().get(fileId=node, fields="id,name,parents").execute()
                trail.append(meta["name"])
                if meta["id"] == sandbox_id or not meta.get("parents"):
                    break
                node = meta["parents"][0]
            report.check(
                "filed under the sandbox, not the real archive",
                SANDBOX_FOLDER in trail,
                " / ".join(reversed(trail)),
            )

    finally:
        # ── PHASE 7 ─────────────────────────────────────────────────────────
        report.phase(7, "Cleanup" + (" (SKIPPED — --keep)" if keep else ""))
        # if/else rather than an early `return`: returning from a `finally` block
        # DISCARDS any exception still in flight, which would hide the very
        # failure the runner needs to report.
        if keep:
            report.note("left in place for inspection. Re-run without --keep to remove.")
        else:
            if drive_file_id:
                drive_delete(report, api, drive_file_id, "the receipt")
            drive_delete(report, api, sandbox_id, f"folder '{SANDBOX_FOLDER}'")

            purge_marked_rows()
            report.note("deleted payments, enrollment, link, student and parent")
            remaining = marked_rows_remaining()
            report.check("no database leftovers", remaining == 0, f"{remaining} row(s) remain")
