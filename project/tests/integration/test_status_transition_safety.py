"""Status transitions that the database, or the audit trail, had a say in.

Three defects with one shape: a bulk `queryset.update()` used where the row had
more to it than the column being written.

* `update()` does not run `full_clean()`, so a constraint violation surfaces as
  a raw `IntegrityError` rather than the constraint's own Spanish message — and
  it aborts the whole statement, so a selection of fifty payments failed
  entirely because one of them collided.
* `update()` does not fire `pre_save` / `post_save`, so `core.audit_signals`
  never saw the change. `Enrollment` is a tracked model, and the audit log
  recorded enrollments being created while recording nothing at all about them
  being cancelled.
* `update()` does not bump an `auto_now` column, so `updated_at` still claimed
  the row was last touched before the change that is visible in it.

None of the three is detectable from the resulting row, which is why they are
pinned here rather than left to the views' own tests.
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.admin.sites import AdminSite
from django.db import IntegrityError, transaction
from django.urls import reverse

from billing.admin import PaymentAdmin
from billing.models import Enrollment, Payment
from billing.services.enrollment_service import EnrollmentService
from core.audit_models import AuditLog

pytestmark = pytest.mark.django_db


class _CollectingAdmin(PaymentAdmin):
    """PaymentAdmin with `message_user` captured instead of pushed onto a
    request's message store — the actions are the unit under test, not Django's
    messages framework."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.messages = []

    def message_user(self, request, message, level=None, **kwargs):
        self.messages.append(message)


def _admin():
    return _CollectingAdmin(Payment, AdminSite())


def _monthly(student, parent, month, status, concept="Cuota"):
    return Payment.objects.create(
        student=student,
        parent=parent,
        payment_type="monthly",
        amount=Decimal("54.00"),
        due_date=date(2031, month, 28),
        concept=concept,
        payment_status=status,
    )


class TestReopeningPaymentsSurvivesTheMonthConstraint:
    """`unique_pending_periodic_payment_per_month` permits ONE pending
    monthly/quarterly row per student per due-month. Cancelling frees that month
    — deliberately, so `reconcile_payment_schedule` can supersede a stale row —
    which means the schedule may already have re-billed it by the time an admin
    reaches for "Restaurar a pendiente". Restoring the cancelled row then
    collides with its own replacement.

    That is the normal aftermath of the documented repair, not an edge case, and
    it used to be an unhandled 500.
    """

    def test_a_colliding_row_is_skipped_instead_of_raising(self, student, parent, active_enrollment):
        cancelled = _monthly(student, parent, 3, "cancelled", "Marzo (cancelado)")
        _monthly(student, parent, 3, "pending", "Marzo (re-emitido)")

        admin = _admin()
        admin.restore_payments(None, Payment.objects.filter(id=cancelled.id))

        cancelled.refresh_from_db()
        assert cancelled.payment_status == "cancelled", "the colliding row must be left alone, not reopened"

    def test_the_rest_of_the_selection_still_goes_through(self, student, parent, active_enrollment):
        """The bug was not only the exception — `update()` is one statement, so
        one collision rolled back every other row in the selection too."""
        colliding = _monthly(student, parent, 3, "cancelled", "Marzo (cancelado)")
        _monthly(student, parent, 3, "pending", "Marzo (re-emitido)")
        free = _monthly(student, parent, 7, "cancelled", "Julio")

        admin = _admin()
        admin.restore_payments(None, Payment.objects.filter(id__in=[colliding.id, free.id]))

        free.refresh_from_db()
        colliding.refresh_from_db()
        assert free.payment_status == "pending"
        assert colliding.payment_status == "cancelled"

    def test_the_admin_is_told_which_rows_were_skipped(self, student, parent, active_enrollment):
        """A silent skip is worse than the crash: the admin would believe the
        payment had been reopened."""
        cancelled = _monthly(student, parent, 3, "cancelled", "Marzo (cancelado)")
        _monthly(student, parent, 3, "pending", "Marzo (re-emitido)")

        admin = _admin()
        admin.restore_payments(None, Payment.objects.filter(id=cancelled.id))

        message = " ".join(admin.messages)
        assert "1 sin reabrir" in message
        assert "Marzo (cancelado)" in message, "the skipped payment must be identifiable"

    def test_two_selected_rows_in_one_month_do_not_collide_with_each_other(self, student, parent, active_enrollment):
        """The occupied-slot set is seeded from rows OUTSIDE the selection, so
        it also has to be updated as the loop claims months — otherwise two
        cancelled rows for the same month both pass the check."""
        first = _monthly(student, parent, 4, "cancelled", "Abril A")
        second = _monthly(student, parent, 4, "cancelled", "Abril B")

        admin = _admin()
        admin.restore_payments(None, Payment.objects.filter(id__in=[first.id, second.id]))

        first.refresh_from_db()
        second.refresh_from_db()
        statuses = sorted([first.payment_status, second.payment_status])
        assert statuses == ["cancelled", "pending"], "exactly one of the two may hold the month"

    def test_a_non_periodic_payment_is_never_blocked(self, student, parent, active_enrollment):
        """The constraint covers `monthly`/`quarterly` only — several
        `enrollment` / `other` payments may legitimately fall due in one month,
        so those must not be filtered by the collision check."""
        first = Payment.objects.create(
            student=student,
            parent=parent,
            payment_type="other",
            amount=Decimal("10.00"),
            due_date=date(2031, 5, 28),
            concept="Material A",
            payment_status="cancelled",
        )
        second = Payment.objects.create(
            student=student,
            parent=parent,
            payment_type="other",
            amount=Decimal("10.00"),
            due_date=date(2031, 5, 28),
            concept="Material B",
            payment_status="cancelled",
        )

        admin = _admin()
        admin.restore_payments(None, Payment.objects.filter(id__in=[first.id, second.id]))

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.payment_status == "pending"
        assert second.payment_status == "pending"

    def test_mark_as_pending_shares_the_same_guard(self, student, parent, active_enrollment):
        """Two actions, one behaviour — they differ only in their label."""
        cancelled = _monthly(student, parent, 3, "cancelled", "Marzo (cancelado)")
        _monthly(student, parent, 3, "pending", "Marzo (re-emitido)")

        admin = _admin()
        admin.mark_as_pending(None, Payment.objects.filter(id=cancelled.id))

        cancelled.refresh_from_db()
        assert cancelled.payment_status == "cancelled"

    def test_reopening_clears_the_payment_date(self, student, parent, active_enrollment):
        """Every income figure filters on `payment_date`; leaving it set on a
        reopened row reported money nobody has paid."""
        completed = _monthly(student, parent, 8, "completed", "Agosto")
        completed.payment_date = date(2031, 8, 5)
        completed.save(update_fields=["payment_date"])

        _admin().restore_payments(None, Payment.objects.filter(id=completed.id))

        completed.refresh_from_db()
        assert completed.payment_status == "pending"
        assert completed.payment_date is None

    def test_the_constraint_really_would_have_fired(self, student, parent, active_enrollment):
        """Guards the guard: if the constraint ever stopped covering this shape,
        the tests above would pass for the wrong reason."""
        cancelled = _monthly(student, parent, 3, "cancelled")
        _monthly(student, parent, 3, "pending")
        with pytest.raises(IntegrityError), transaction.atomic():
            Payment.objects.filter(id=cancelled.id).update(payment_status="pending")


class TestEnrollmentStatusChangesAreAudited:
    """`Enrollment` is in `core.audit_signals._TRACKED`, but every status
    transition went through `queryset.update()`, which bypasses the receivers.
    Creations were logged and cancellations were not — precisely the half you go
    to the audit log to find.
    """

    def _audit_updates(self):
        return AuditLog.objects.filter(model="billing.Enrollment", action="update")

    def test_cancelling_writes_an_audit_row(self, student, active_enrollment):
        before = self._audit_updates().count()

        closed = EnrollmentService.close_active_enrollments(student, "cancelled")

        assert closed == 1
        assert self._audit_updates().count() == before + 1

    def test_the_audit_row_records_the_transition(self, student, active_enrollment):
        EnrollmentService.close_active_enrollments(student, "cancelled")

        entry = self._audit_updates().latest("created_at")
        assert entry.changes["status"] == ["active", "cancelled"]

    def test_updated_at_is_bumped(self, student, active_enrollment):
        """`auto_now` only fires on `save()`, so the bulk update left the row
        claiming it had not been touched since before the change in it."""
        before = active_enrollment.updated_at

        EnrollmentService.close_active_enrollments(student, "finished")

        active_enrollment.refresh_from_db()
        assert active_enrollment.updated_at > before

    def test_only_active_enrollments_are_touched(self, student, cancelled_enrollment):
        closed = EnrollmentService.close_active_enrollments(student, "finished")

        cancelled_enrollment.refresh_from_db()
        assert closed == 0
        assert cancelled_enrollment.status == "cancelled"

    def test_it_returns_the_count_the_view_reports(self, student, active_enrollment):
        """`add_to_waiting_list` phrases its success message on this number."""
        assert EnrollmentService.close_active_enrollments(student, "cancelled") == 1
        assert EnrollmentService.close_active_enrollments(student, "cancelled") == 0

    def test_moving_a_student_to_the_waiting_list_is_audited_end_to_end(
        self, authenticated_client, student, active_enrollment
    ):
        """The view path, not just the service — this is the transition that
        used to be a one-way door and left no trace of who opened it."""
        before = self._audit_updates().count()

        response = authenticated_client.post(reverse("add_to_waiting_list", args=[student.id]))

        assert response.status_code == 302
        assert Enrollment.objects.get(id=active_enrollment.id).status == "cancelled"
        assert self._audit_updates().count() == before + 1
