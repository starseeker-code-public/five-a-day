"""Who the weekly payment reminder reaches, and over what window.

Three rules, each of which was wrong: a debt stopped being chased the moment it
became one (`due_date__gte=today`), adult students were never reminded at all
(no Parent row, and only `payment.parent.email` was read), and two consecutive
INCLUSIVE windows overlapped on a day, so a Monday due date was reminded twice.

From the v1.29.5 review.

One file per review round, like `test_stripe_portal_and_audit_regressions.py` and
`test_payment_lifecycle_rules.py`. Each class names the defect it pins, not the
function it happens to call, because the point of these is that the specific
failure cannot come back.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from billing.models import Enrollment, Payment
from comms.tasks import send_payment_reminders
from conftest import current_course_year

pytestmark = pytest.mark.django_db


@pytest.fixture
def adult_enrollment(db, adult_student, enrollment_type_adults, site_config):
    """An active enrollment for the `adult_student` fixture.

    Local rather than in conftest: only these tests need it. The academic year
    is anchored with `current_course_year()` — a hard-coded "2025-2026" is a
    date bomb (see CLAUDE.md), it falls out of `relevant_academic_years()` the
    moment the course rolls over.
    """

    academic_year, start_year = current_course_year()
    return Enrollment.objects.create(
        student=adult_student,
        enrollment_type=enrollment_type_adults,
        enrollment_period_start=date(start_year, 9, 15),
        enrollment_period_end=date(start_year + 1, 6, 27),
        academic_year=academic_year,
        schedule_type="adult_group",
        payment_modality="monthly",
        enrollment_amount=Decimal("54.00"),
        final_amount=Decimal("54.00"),
        status="active",
        enrollment_date=date(start_year, 9, 1),
    )


def _pending(student, parent, enrollment, due, amount="54.00", concept="Mensualidad"):
    return Payment.objects.create(
        student=student,
        parent=parent,
        enrollment=enrollment,
        payment_type="other",  # `other` sidesteps the pending-periodic-per-month
        payment_method="transfer",  # unique index — these tests are about the
        amount=Decimal(amount),  # reminder window, not about billing idempotency.
        payment_status="pending",
        due_date=due,
        concept=concept,
    )


def _run_reminders():
    """Run the task with the mailer stubbed, returning the captured contexts.

    Returns `(recipients, contexts, result)` — the bulk sender is the only thing
    patched, so the queryset, the window and the recipient resolution are all
    the real ones.
    """

    captured: list[dict] = []

    def _capture(template_name, emails_data, fail_silently=True):
        captured.extend(emails_data)
        return {"sent": len(emails_data), "failed": 0}

    with patch("comms.services.email_service.EmailService.send_bulk_emails", side_effect=_capture):
        result = send_payment_reminders.run()

    return [c["recipient"] for c in captured], [c["context"] for c in captured], result


# ── HIGH: the reminder never reached an overdue family or an adult ──────────


class TestOverduePaymentsAreStillChased:
    """`due_date__gte=today` meant a debt stopped being chased at the exact
    moment it became a debt. Every income figure counts these as expected
    revenue; nothing was asking for them."""

    def test_an_overdue_payment_is_reminded(self, student_with_parent, active_enrollment, site_config):
        parent = student_with_parent.parents.first()
        _pending(student_with_parent, parent, active_enrollment, date.today() - timedelta(days=45))

        recipients, contexts, result = _run_reminders()

        assert parent.email in recipients
        assert result["sent"] == 1
        assert contexts[0]["is_overdue"] is True
        assert contexts[0]["days_overdue"] == 45

    def test_a_future_payment_is_not_flagged_overdue(self, student_with_parent, active_enrollment, site_config):
        parent = student_with_parent.parents.first()
        _pending(student_with_parent, parent, active_enrollment, date.today() + timedelta(days=3))

        _, contexts, _ = _run_reminders()

        assert contexts[0]["is_overdue"] is False
        assert contexts[0]["days_overdue"] == 0

    def test_a_completed_overdue_row_is_never_chased(self, student_with_parent, active_enrollment, site_config):
        """`payment_status="pending"` is the ONLY exit condition now that the
        lower bound is gone — so it has to be the thing that stops the chase."""
        parent = student_with_parent.parents.first()
        p = _pending(student_with_parent, parent, active_enrollment, date.today() - timedelta(days=45))
        Payment.objects.filter(pk=p.pk).update(payment_status="completed", payment_date=date.today())

        _, _, result = _run_reminders()

        assert result["status"] == "no_pending_payments"

    def test_a_cancelled_overdue_row_is_never_chased(self, student_with_parent, active_enrollment, site_config):
        parent = student_with_parent.parents.first()
        p = _pending(student_with_parent, parent, active_enrollment, date.today() - timedelta(days=45))
        Payment.objects.filter(pk=p.pk).update(payment_status="cancelled")

        _, _, result = _run_reminders()

        assert result["status"] == "no_pending_payments"


class TestAdultStudentsAreReminded:
    """An adult has no `Parent` row — `Payment.parent` is nullable precisely for
    them — and the loop read only `payment.parent.email`, so they were never
    reminded of anything. Same fallback the receipt task uses."""

    def test_adult_student_gets_the_reminder_at_their_own_address(self, adult_student, adult_enrollment, site_config):
        _pending(adult_student, None, adult_enrollment, date.today() + timedelta(days=3))

        recipients, _, result = _run_reminders()

        assert recipients == [adult_student.email]
        assert result["sent"] == 1

    def test_an_adult_with_no_email_is_skipped_not_crashed(self, adult_student, adult_enrollment, site_config):
        adult_student.email = ""
        adult_student.save(update_fields=["email"])
        _pending(adult_student, None, adult_enrollment, date.today() + timedelta(days=3))

        recipients, _, result = _run_reminders()

        assert recipients == []
        assert result["sent"] == 0

    def test_no_sms_is_attempted_for_a_parentless_payment(self, adult_student, adult_enrollment, site_config):
        """SMS opt-in lives on `Parent`; there is no parent to have opted in."""
        _pending(adult_student, None, adult_enrollment, date.today() + timedelta(days=3))

        with patch("comms.tasks.send_payment_reminder_sms_task.delay") as sms:
            _, _, result = _run_reminders()

        sms.assert_not_called()
        assert result["sms_queued"] == 0


class TestReminderWindowIsHalfOpen:
    """Consecutive weekly runs of an INCLUSIVE `today..today+7` overlapped on
    exactly one day, so a payment falling due on that weekday was reminded
    twice. `[today, today+7)` is seven distinct days and the next run starts
    where this one stopped."""

    def test_the_boundary_day_belongs_to_the_next_run(self, student_with_parent, active_enrollment, site_config):
        parent = student_with_parent.parents.first()
        _pending(student_with_parent, parent, active_enrollment, date.today() + timedelta(days=7))

        _, _, result = _run_reminders()

        assert result["status"] == "no_pending_payments", "due_date == today+7 belongs to next week's window"

    def test_the_last_day_inside_the_window_is_included(self, student_with_parent, active_enrollment, site_config):
        """The other half of the same rule: half-open must not leave a GAP, or a
        payment falls between two runs and is never announced before it is due."""
        parent = student_with_parent.parents.first()
        _pending(student_with_parent, parent, active_enrollment, date.today() + timedelta(days=6))

        _, _, result = _run_reminders()

        assert result["sent"] == 1
