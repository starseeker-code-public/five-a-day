"""Core Celery tasks."""

from datetime import timedelta
from io import StringIO

from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from billing.constants import RECEIPT_RELATIONS
from billing.models import Payment
from billing.services.pdf_service import generate_payment_receipt
from comms.services.email_functions import send_fun_friday_email
from comms.services.email_service import email_service
from comms.tasks import send_payment_receipt_email_task
from core.models import AuditLog, BacklogTask, FunFridayScheduledSend
from core.services.drive_service import drive_uploads_allowed
from core.services.drive_service import get_service as get_drive_service

logger = get_task_logger(__name__)

# Two years keeps a full academic-year comparison available while bounding the
# table. Long enough to answer "who changed this student's fee last September?".
AUDIT_LOG_RETENTION_DAYS = 730

# The shortest retention `prune_audit_log` will accept. A full academic year plus
# a margin, so a pruning run can never destroy the trail for the course being
# taught — which is the one an audit question is most likely to be about.
MIN_AUDIT_RETENTION_DAYS = 400


@shared_task(name="core.tasks.cleanup_done_backlog_tasks")
def cleanup_done_backlog_tasks(days: int = 30):
    """Delete QA backlog tasks that have been marked 'done' for over `days` days.

    Scheduled daily by Celery Beat. `updated_at` is the time the task was last
    changed, i.e. when it was marked done, so it stands in for a completion date.
    """

    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = BacklogTask.objects.filter(status="done", updated_at__lt=cutoff).delete()
    logger.info("Deleted %d completed backlog task(s) older than %d days", deleted, days)
    return {"status": "success", "deleted": deleted}


@shared_task(name="core.tasks.reset_tester_environment_task")
def reset_tester_environment_task():
    """Nightly rebuild of the public tester sandbox. TESTING ONLY.

    THE ENVIRONMENT GATE IS THE WHOLE POINT OF THIS WRAPPER. Celery Beat runs in
    development too — `docker-compose.yml` starts `celery_beat` in every
    environment that uses it — so an ungated entry in `beat_schedule` would wipe
    every developer's local database at 07:30 each morning, silently, with the
    only evidence being data that "went missing overnight". The command itself
    refuses only PRODUCTION (it is a legitimate manual tool in development), so
    the narrower "unattended runs happen on the QA VM and nowhere else" rule has
    to live here, at the scheduled entry point.

    Production has no Beat process at all, and this deliberately gets NO Cloud
    Run Job / Cloud Scheduler entry, which is a knowing exception to the rule
    that every Beat task needs a wrapper command provisioned in production. The
    command exists for local and QA use; production must never be able to run it.

    07:30 Europe/Madrid, chosen around three other things: the nightly testing
    deploy owns 01:00-05:59 and a reset landing mid-migration would race it; the
    06:00-07:00 Beat cluster (payments, expenses, backlog cleanup) should have
    finished; and birthday emails go at 08:00, so the roll is rebuilt before
    anything reads it.
    """
    if not settings.IS_TESTING_ENV:
        logger.info("reset_tester_environment_task skipped: not the testing environment")
        return {"status": "skipped", "reason": "not testing environment"}

    out = StringIO()
    call_command("reset_tester_environment", stdout=out)
    logger.info("Tester sandbox rebuilt by the nightly task")
    return {"status": "success", "output": out.getvalue()}


@shared_task(name="core.tasks.prune_audit_log")
def prune_audit_log(days: int = AUDIT_LOG_RETENTION_DAYS, dry_run: bool = False):
    """Delete AuditLog rows older than `days`.

    The audit trail had no cap and no pruning of any kind, unlike HistoryLog
    (capped at 1,000 rows). It grows fast — scheduling one student's academic
    year of payments writes 16 rows on its own — so at the documented 2,000
    student scale it would become the largest table in the database with
    nothing ever removing a row.

    `days` must be at least MIN_AUDIT_RETENTION_DAYS. This is the only code path
    that deletes from a table the admin deliberately makes immutable (no add, no
    change, no delete — see `core.admin`), so the whole point is that ageing rows
    out is a policy and not a person. `days=0` would delete every row including
    today's, and a negative value reaches into the future; neither is a retention
    policy, both are a typo, and either one erases the entries incriminating
    whoever ran it. Refusing them is cheaper than restoring a backup.
    """

    if days < MIN_AUDIT_RETENTION_DAYS:
        raise ValueError(
            f"days must be >= {MIN_AUDIT_RETENTION_DAYS} to prune the audit log "
            f"(got {days}); a smaller window would erase the recent trail."
        )

    cutoff = timezone.now() - timedelta(days=days)
    stale = AuditLog.objects.filter(created_at__lt=cutoff)

    if dry_run:
        count = stale.count()
        logger.info("[dry-run] Would delete %d audit log row(s) older than %d days", count, days)
        return {"status": "success", "deleted": 0, "would_delete": count, "dry_run": True}

    deleted, _ = stale.delete()
    logger.info("Deleted %d audit log row(s) older than %d days", deleted, days)
    return {"status": "success", "deleted": deleted, "dry_run": False}


@shared_task(name="core.tasks.purge_expired_sessions")
def purge_expired_sessions():
    """Delete expired `django_session` rows.

    Nothing purged this table before v1.23.0, and that matters more than
    ordinary table growth because it holds authentication material:
    `django_session` is the DEFAULT database session backend, and session
    payloads are base64-encoded JSON — signed, not encrypted. Anything a view
    puts in the session is readable by anyone who can read the table, and rows
    outlived their cookies indefinitely.

    Django ships `clearsessions` for this; the task wraps it so the work is
    scheduled the same way as every other periodic job.

    It used to purge `parent_session_tokens` too. That table is gone: the parent
    portal issues a temporary PASSWORD rather than a set-password link, and the
    hash lives in a column on `parents` that `set_portal_password` clears — so
    there is no longer a side table of spent credentials to sweep.
    """

    call_command("clearsessions")

    logger.info("Purged expired sessions")
    return {"status": "success"}


# NOTE: the old `send_fun_friday_emails_task` (an immediate fan-out taking a raw
# `recipients` list) was removed. It had NO code callers, but was advertised as
# the "manual send" path while bypassing the `FunFridayScheduledSend` claim guard
# (`WHERE sent_at IS NULL`) that `_send_fun_friday_batch` implements below — so
# anyone following the README could double-mail every family. All sends now go
# through a persisted `FunFridayScheduledSend` row (drained immediately if its
# slot has already passed), so the claim guard always applies.


def _send_fun_friday_batch(
    recipients: list,
    day_name: str,
    day_number: int,
    month: str,
    start_time: str,
    end_time: str,
    activity_description: str,
    minimum_age=None,
    maximum_age=None,
    meeting_point=None,
) -> dict:
    """Send one Fun Friday announcement batch. Shared by the direct task and the drain task.

    ONE SMTP session for the whole announcement: this loop is the academy's
    largest single batch (every family), and it was paying a TCP+TLS+AUTH
    handshake per address. Opening the connection is wrapped because `open()`
    is not `fail_silently` — an unwrapped failure here would abort a drain whose
    row is already CLAIMED, i.e. lose the announcement outright.
    """

    connection = None
    try:
        connection = email_service.open_connection()
        connection.open()
    except Exception:
        logger.exception("Fun Friday batch: SMTP connection could not be opened")
        return {"status": "failed", "sent": 0, "total": len(recipients)}

    sent = 0
    try:
        for index, email in enumerate(recipients, start=1):
            try:
                if send_fun_friday_email(
                    recipients=email,
                    day_name=day_name,
                    day_number=day_number,
                    month=month,
                    start_time=start_time,
                    end_time=end_time,
                    activity_description=activity_description,
                    minimum_age=minimum_age,
                    maximum_age=maximum_age,
                    meeting_point=meeting_point,
                    connection=connection,
                ):
                    sent += 1
            except Exception:  # one bad recipient must not abort the batch
                # Opaque index, not the address: recipient emails are family PII
                # and Cloud Logging retention outlives the app's own controls
                # (this file's own rule — see the module docstring on args).
                logger.exception("Fun Friday email failed for recipient %d of %d", index, len(recipients))
    finally:
        try:
            connection.close()
        except Exception:
            logger.exception("Fun Friday batch: SMTP connection could not be closed")

    logger.info("Fun Friday emails sent: %d/%d", sent, len(recipients))
    return {"status": "success", "sent": sent, "total": len(recipients)}


@shared_task(name="core.tasks.send_due_fun_friday_emails_task", bind=True)
def send_due_fun_friday_emails_task(self):
    """Send every ``FunFridayScheduledSend`` whose scheduled time has passed.

    Idempotent: rows are marked ``sent_at`` and never re-sent. Runs via
    Celery Beat (daily 14:30) in dev/testing and via the
    ``send_due_fun_friday_emails`` management command in production.
    """

    due_ids = list(
        FunFridayScheduledSend.objects.filter(sent_at__isnull=True, scheduled_for__lte=timezone.now()).values_list(
            "id", flat=True
        )
    )

    processed = 0
    sent_total = 0
    failed = 0
    for row_id in due_ids:
        # CLAIM the row before sending, with a conditional UPDATE that only
        # matches while sent_at IS NULL. Marking it after the send left a
        # window where two overlapping drains (the immediate .delay() from the
        # form and the scheduled Beat/Cloud Scheduler run, which both fire at
        # 14:30) each saw sent_at=None and mailed every parent twice.
        claimed = FunFridayScheduledSend.objects.filter(id=row_id, sent_at__isnull=True).update(sent_at=timezone.now())
        if not claimed:
            continue  # another worker got there first
        scheduled = FunFridayScheduledSend.objects.get(id=row_id)

        # The row is CLAIMED above, before sending. If the send then raises, the
        # row stays claimed on purpose: `_send_fun_friday_batch` mails parents
        # one at a time and may have delivered some already, so releasing the
        # claim would re-mail them. Losing an announcement is recoverable by
        # hand; sending it twice to every family is not.
        #
        # The `try` is what stops one bad row taking the whole drain down —
        # previously an exception here aborted the loop and every later due row
        # silently went unsent, with its claim already written.
        try:
            result = _send_fun_friday_batch(
                recipients=scheduled.recipients,
                day_name=scheduled.day_name,
                day_number=scheduled.day_number,
                month=scheduled.month,
                start_time=scheduled.start_time,
                end_time=scheduled.end_time,
                activity_description=scheduled.activity_description,
                minimum_age=scheduled.minimum_age,
                maximum_age=scheduled.maximum_age,
                meeting_point=scheduled.meeting_point,
            )
        except Exception:
            failed += 1
            logger.exception(
                "Fun Friday scheduled send %d failed AFTER being claimed — it will not retry. "
                "Re-create it from /apps/ if the announcement still needs to go out.",
                int(row_id),
            )
            continue

        processed += 1
        sent_total += result["sent"]

    if processed or failed:
        logger.info(
            "Fun Friday drain: %d scheduled send(s) processed, %d email(s) sent, %d failed",
            processed,
            sent_total,
            failed,
        )
    return {"status": "success", "processed": processed, "sent": sent_total, "failed": failed}


# ---------------------------------------------------------------------------
# Payment completion side effects
#
# "What happens when money lands" lives HERE, not in `comms.tasks`, for one
# reason: one of the two side effects is a Google Drive archive, and the Drive
# service is a core service that reads a core model (the QA upload toggle). With
# the dispatcher in comms, `comms` had to import `core` at module level —
# backwards against the dependency flow in CLAUDE.md, and the same shape of debt
# as the Fun Friday drain that used to live there.
#
# The receipt EMAIL task stays in `comms.tasks` where it belongs; core may
# import comms, so the dispatcher can reach both from here. Its three callers
# (`core.views.payments`, `billing.admin`, `billing.services.stripe_service`)
# already import core elsewhere, so nothing new is introduced by the move.
#
# It must remain the ONE statement of this pair: there are two completion paths
# in the app, and a third side effect written twice would be right once and
# silently half-wrong once.
# ---------------------------------------------------------------------------


def dispatch_payment_completed_on_commit(payment_id: int) -> None:
    """`dispatch_payment_completed`, deferred to COMMIT.

    Production runs ``CELERY_TASK_ALWAYS_EAGER=True`` (Cloud Run, no worker), so
    ``.delay()`` executes the task *here* — it re-reads the payment by id and
    emails a receipt. Called from inside a ``transaction.atomic()`` block that
    later rolls back, that is a receipt for money the database does not record;
    with a real broker it is a worker reading the row before the write is
    visible. ``transaction.on_commit`` runs the callback immediately when there
    is no open transaction, so callers outside one behave exactly as before.

    It lives HERE, next to the dispatch it defers, rather than in
    ``core/views/payments.py`` where it started life as ``_queue_payment_receipt``:
    ``billing/admin.py`` is the second completion path in the app and had to
    reach into a view module for a private helper to get its receipts sent.
    A completion side effect is not a view concern.

    Consequence for tests: under the plain ``django_db`` fixture on_commit
    callbacks never fire, so anything asserting ``mail.outbox`` needs
    ``django_capture_on_commit_callbacks(execute=True)``.
    """

    def _dispatch():
        dispatch_payment_completed(int(payment_id))

    transaction.on_commit(_dispatch)


def dispatch_payment_completed(payment_id: int) -> None:
    """Fire every side effect of a payment becoming COMPLETED: receipt + archive.

    The one statement of "what happens when money lands", because there are two
    completion paths — the admin marking a payment cobrado and the Stripe webhook
    — and each carried its own copy of this pair of dispatches. A third side
    effect, or a third completion path, would have had to be written twice to be
    right and once to be silently half-wrong.

    Each dispatch gets its own ``try``: a Drive outage must not cost the family
    their receipt email, and vice versa. Neither may raise, because production
    runs ``CELERY_TASK_ALWAYS_EAGER`` — the "queue" is this call stack, inside
    the request that recorded the money.

    Callers inside a transaction must go through
    ``dispatch_payment_completed_on_commit`` above: eager execution re-reads the
    payment by id, which a not-yet-committed write is invisible to.
    """
    try:
        send_payment_receipt_email_task.delay(int(payment_id))
    except Exception:  # receipt is nice-to-have
        logger.exception("Failed to enqueue payment receipt for payment %d", int(payment_id))

    try:
        upload_receipt_to_drive_task.delay(int(payment_id))
    except Exception:  # Drive archive is nice-to-have
        logger.exception("Failed to enqueue Drive receipt upload for payment %d", int(payment_id))


@shared_task(name="core.tasks.upload_receipt_to_drive_task", bind=True)
def upload_receipt_to_drive_task(self, payment_id: int):
    """Best-effort: archive a completed payment's receipt PDF to Google Drive.

    A no-op outside production unless the QA VM's `/testing/` toggle is on, in
    which case the receipt is filed into the month's `testing/` sandbox — see
    `core.services.drive_service.drive_uploads_allowed`.

    Deliberately NOT auto-retrying and NOT raising: the Drive archive is a
    convenience on top of the `Payment` row and the emailed receipt, so a Drive
    problem must never fail the payment flow or spawn a retry storm (production
    runs eager, so a raise here would surface inside the completion request). The
    upload service already swallows every error and returns a status; this task
    just resolves the payment, renders the PDF and records the outcome.
    """

    # Same gate the service enforces, asked here too so a disallowed environment
    # never even loads the payment or renders its PDF (production runs eager, so
    # that work happens inside the "marcar cobrado" request).
    if not drive_uploads_allowed():
        return {"status": "disabled", "payment_id": payment_id}

    drive = get_drive_service()
    if not drive.is_configured():
        return {"status": "not_configured", "payment_id": payment_id}

    try:
        payment = Payment.objects.select_related(*RECEIPT_RELATIONS).get(id=payment_id)
    except Payment.DoesNotExist:
        logger.warning("upload_receipt_to_drive_task: payment %s not found", payment_id)
        return {"status": "error", "message": "payment not found"}

    # Only completed payments have a real receipt to archive.
    if payment.payment_status != "completed":
        return {"status": "skipped", "reason": "not completed", "payment_id": payment_id}

    try:
        pdf_bytes = generate_payment_receipt(payment)
    except Exception:
        # Rendering failing is worth knowing about, but still must not blow up the
        # completion flow — log and stop.
        logger.exception("upload_receipt_to_drive_task: failed to render PDF for payment %s", payment_id)
        return {"status": "error", "message": "pdf render failed", "payment_id": payment_id}

    result = drive.upload_receipt(payment, pdf_bytes)
    if not result.success and result.status == "error":
        # result.error is one of the service's own fixed messages, not user input.
        logger.warning("upload_receipt_to_drive_task: payment %s not archived (%s)", payment_id, result.error)
    return {"payment_id": payment_id, **result.as_dict()}
