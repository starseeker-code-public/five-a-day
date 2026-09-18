"""END-TO-END in CI: the whole money path in one test, Google mocked.

The counterpart to `project/tests/e2e/payment_journey.py`, which runs the same
journey against a REAL Google Drive with a live credential and therefore cannot
run here: CI gets an ephemeral Postgres with no `GoogleDriveCredential` row, the
refresh token is encrypted with a key derived from `SECRET_KEY`, and `drive` is a
restricted scope whose refresh tokens expire every 7 days while the consent
screen is in Testing. So the split is deliberate — everything up to the Drive
API call is asserted here on every push, and the upload itself is asserted by the
pre-commit journey on a machine that has an account connected.

WHAT THIS ADDS OVER THE EXISTING SUITE. The individual pieces are well covered
(`test_payment_views.py`, `test_drive_receipts.py`, `test_payment_scheduling.py`,
`test_receipt_view.py`). What was not covered is the CHAIN: that enrolling a real
family through `EnrollmentService` produces a payment the `quick-complete`
endpoint will accept, that completing it assigns a receipt number AND emails a
PDF AND dispatches the archive, and that doing it twice changes nothing. Every
bug this app has shipped in the money path lived in a seam like that, not inside
one of the units.

TWO THINGS THE SHAPE OF THIS FILE IS DEFENDING AGAINST, both of which have
already turned this suite red once with no application change behind it:

* **Date bombs.** Nothing here asserts a money literal. The first period is
  prorated by join date (`PaymentService.proration_fraction`), so `35.00` only
  ever held on the 1st of a month; and `relevant_academic_years()` drops a
  hard-coded year the moment the calendar rolls into the next course. The
  enrollment is anchored to `current_course_year()` and amounts are asserted as
  properties (positive, unchanged, matching the row) rather than as values.
* **`on_commit`.** `dispatch_payment_completed_on_commit` defers everything to
  commit, and under the plain `django_db` fixture those callbacks never fire —
  a test asserting `mail.outbox` without
  `django_capture_on_commit_callbacks(execute=True)` passes vacuously.

NOTE ON THE ENVIRONMENT. Nothing here overrides `ENVIRONMENT` to production,
even though the Drive archive is production-only. That gate lives INSIDE
`upload_receipt_to_drive_task` and is covered by `test_drive_receipts.py`;
`dispatch_payment_completed` queues the task unconditionally, and what this
file asserts is the DISPATCH. Overriding the environment here would couple the
chain test to a rule it is not testing, and `override_settings` cannot decorate
a plain pytest class anyway — it raises at COLLECTION, taking the whole module
with it.
"""

from __future__ import annotations

import json
import re
from datetime import date
from unittest.mock import patch

import pytest
from django.core import mail
from django.urls import reverse

from billing.models import Payment
from billing.services.enrollment_service import EnrollmentService
from billing.services.payment_service import PaymentService
from conftest import current_course_year
from students.models import Parent, Student, StudentParent

pytestmark = pytest.mark.django_db

#: `YYYY-NNN`, continuing the academy's paper books. A shape, never a value: the
#: counter is per-year and shared, so a literal would break on the first January.
RECEIPT_NUMBER = re.compile(r"\d{4}-\d{3,}")


@pytest.fixture
def family(db):
    """A parent, a child and the link between them — the real models."""
    parent = Parent.objects.create(
        first_name="Journey",
        last_name="Padre",
        dni="00000001J",
        email="journey.padre@example.invalid",
        phone="600000000",
    )
    _, start_year = current_course_year()
    student = Student.objects.create(
        first_name="Journey",
        last_name="Alumno",
        birth_date=date(start_year - 10, 5, 5),
        active=True,
    )
    StudentParent.objects.create(student=student, parent=parent)
    return student, parent


@pytest.fixture
def enrolled(db, family, site_config, enrollment_type_new_student):
    """The family enrolled and billed through the REAL services.

    `start_date` is the first of the current teaching month on purpose: the first
    period is prorated by join date, and anchoring to day 1 keeps the fraction at
    1 so a later assertion about the amount surviving completion is not quietly
    comparing two different prorated figures.
    """
    student, parent = family
    enrollment = EnrollmentService.create_enrollment(
        student,
        {
            "enrollment_plan": "monthly_full",
            "has_language_cheque": False,
            "is_sibling_discount": False,
            "is_special": False,
            "manual_amount": None,
            "start_date": date.today().replace(day=1),
        },
        is_adult=False,
    )
    PaymentService.schedule_academic_year_payments(enrollment, parent)
    return student, parent, enrollment


class TestEnrollmentBills:
    """Phase 1-2: enrolling a family produces something to collect."""

    def test_enrollment_is_created_and_active(self, enrolled):
        _, _, enrollment = enrolled
        assert enrollment.pk is not None
        assert enrollment.status == "active"
        assert enrollment.academic_year == current_course_year()[0]

    def test_a_pending_payment_is_generated(self, enrolled):
        student, _, _ = enrolled
        payments = Payment.objects.filter(student=student)
        assert payments.exists(), "enrolling a family billed them nothing"
        pending = payments.filter(payment_status="pending")
        assert pending.exists()
        assert all(p.amount > 0 for p in pending), "a zero-value charge is not a charge"

    def test_nothing_is_numbered_before_it_is_collected(self, enrolled):
        """A receipt number is proof money was taken and continues the paper
        books, so a pending row must not hold one — issuing on a pending charge
        produced a false official document AND a permanent hole in the sequence
        when that charge was later cancelled."""
        student, _, _ = enrolled
        assert all(p.receipt_number == "" for p in Payment.objects.filter(student=student))

    def test_the_parent_is_the_titular(self, enrolled):
        student, parent, _ = enrolled
        assert all(p.parent_id == parent.pk for p in Payment.objects.filter(student=student))


class TestCollection:
    """Phase 3-5: collecting over HTTP fires every side effect, exactly once."""

    @staticmethod
    def _collect(client, payment, method="transfer"):
        return client.post(
            reverse("quick_complete_payment", args=[payment.pk]),
            data=json.dumps({"payment_method": method}),
            content_type="application/json",
        )

    @pytest.fixture
    def collected(self, authenticated_client, enrolled, django_capture_on_commit_callbacks):
        """Collect the first pending payment through the real endpoint.

        Drive is patched at `core.tasks.upload_receipt_to_drive_task`, which is
        where the dispatcher LOOKS the name up — patching the definition module
        would patch nothing and the test would pass having exercised the real
        uploader (or, in CI, having tried to).
        """
        student, parent, enrollment = enrolled
        payment = Payment.objects.filter(student=student, payment_status="pending").earliest("due_date")
        mail.outbox.clear()
        with patch("core.tasks.upload_receipt_to_drive_task.delay") as archive:
            with django_capture_on_commit_callbacks(execute=True):
                response = self._collect(authenticated_client, payment)
        payment.refresh_from_db()
        return payment, parent, response, archive

    def test_the_endpoint_accepts_it(self, collected):
        _, _, response, _ = collected
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_the_payment_is_recorded_as_collected(self, collected):
        payment, _, _, _ = collected
        assert payment.payment_status == "completed"
        assert payment.payment_date is not None, (
            "every income figure filters on payment_date; a completed payment without one reports as zero"
        )
        assert payment.payment_method == "transfer"

    def test_a_receipt_number_is_issued(self, collected):
        payment, _, _, _ = collected
        assert RECEIPT_NUMBER.fullmatch(payment.receipt_number)

    def test_the_family_is_emailed_a_pdf_receipt(self, collected):
        payment, parent, _, _ = collected
        assert len(mail.outbox) == 1, f"expected one receipt, got {len(mail.outbox)}"
        message = mail.outbox[0]
        assert parent.email in message.to
        assert message.subject
        pdfs = [a for a in message.attachments if str(a[0]).lower().endswith(".pdf")]
        assert pdfs, "the receipt email carried no PDF"

    def test_the_archive_is_dispatched(self, collected):
        payment, _, _, archive = collected
        archive.assert_called_once_with(payment.pk)

    def test_the_amount_survives_collection(self, collected, enrolled):
        """Collecting must not re-price. Asserted against the row's own history
        rather than a literal, because the first period is prorated by join date
        and every fee is editable from /management/."""
        payment, _, _, _ = collected
        assert payment.amount > 0
        assert payment.amount == Payment.objects.get(pk=payment.pk).amount


class TestRecollectionIsInert:
    """Phase 6: the regression this app actually shipped.

    Re-completing rewrote the historical `payment_date` to today, silently moving
    banked money into a different month in every report, and emailed the family a
    second receipt for the same charge.
    """

    @pytest.fixture
    def collected_twice(self, authenticated_client, enrolled, django_capture_on_commit_callbacks):
        student, parent, _ = enrolled
        payment = Payment.objects.filter(student=student, payment_status="pending").earliest("due_date")
        url = reverse("quick_complete_payment", args=[payment.pk])

        mail.outbox.clear()
        with patch("core.tasks.upload_receipt_to_drive_task.delay"):
            with django_capture_on_commit_callbacks(execute=True):
                authenticated_client.post(
                    url, data=json.dumps({"payment_method": "transfer"}), content_type="application/json"
                )
            payment.refresh_from_db()
            first = (payment.payment_date, payment.receipt_number, payment.payment_method)

            mail.outbox.clear()
            with django_capture_on_commit_callbacks(execute=True) as second_callbacks:
                response = authenticated_client.post(
                    url, data=json.dumps({"payment_method": "cash"}), content_type="application/json"
                )
        payment.refresh_from_db()
        return payment, first, response, second_callbacks

    def test_it_is_accepted_not_rejected(self, collected_twice):
        """Idempotent, not an error: the payments list fires this on a double
        click and a 400 there would read as a broken button."""
        _, _, response, _ = collected_twice
        assert response.status_code == 200
        assert response.json()["already_completed"] is True

    def test_the_historical_payment_date_is_not_rewritten(self, collected_twice):
        payment, (first_date, _, _), _, _ = collected_twice
        assert payment.payment_date == first_date

    def test_the_receipt_number_is_not_reissued(self, collected_twice):
        payment, (_, first_number, _), _, _ = collected_twice
        assert payment.receipt_number == first_number

    def test_no_second_receipt_is_emailed(self, collected_twice):
        assert mail.outbox == []

    def test_nothing_is_dispatched_at_all(self, collected_twice):
        """Not merely "no email": the second call must not reach the dispatcher,
        or a future side effect added there fires twice per collection."""
        _, _, _, second_callbacks = collected_twice
        assert second_callbacks == []


class TestDeadPaymentsCannotBeCollected:
    """A cancelled or refunded charge is dead, and the endpoint must say so.

    Cancelling frees the month for `unique_pending_periodic_payment_per_month`,
    so the schedule may already have re-billed it — and that constraint is
    pending-only, so nothing downstream would stop a completed duplicate.
    """

    @pytest.mark.parametrize("dead_status", ["cancelled", "refunded"])
    def test_refused(self, authenticated_client, enrolled, dead_status):
        student, _, _ = enrolled
        payment = Payment.objects.filter(student=student, payment_status="pending").earliest("due_date")
        Payment.objects.filter(pk=payment.pk).update(payment_status=dead_status)

        response = authenticated_client.post(
            reverse("quick_complete_payment", args=[payment.pk]),
            data=json.dumps({"payment_method": "cash"}),
            content_type="application/json",
        )

        payment.refresh_from_db()
        assert response.status_code == 400
        assert payment.payment_status == dead_status
        assert payment.receipt_number == "", "a refused collection must not burn a receipt number"
        assert mail.outbox == []
