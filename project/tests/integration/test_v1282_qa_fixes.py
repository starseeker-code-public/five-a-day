"""Regression tests for the v1.28.2 QA fix pass.

Covers the behaviours QA reported broken: manual payments must be born pending,
waiting-list entries must be removable, prior students must be re-enrollable in
bulk, receipts must carry a YYYY-NNN number that continues the 2026 paper
sequence, and the receipt must show precio − descuentos = importe.
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from billing.models import Enrollment, Payment
from students.models import Student

pytestmark = pytest.mark.django_db


def _completed_payment(student, parent, enrollment, when):
    """A collected monthly payment on `when` — the shape receipt numbering needs."""
    return Payment.objects.create(
        student=student,
        parent=parent,
        enrollment=enrollment,
        payment_type="monthly",
        payment_method="transfer",
        amount=Decimal("54.00"),
        payment_status="completed",
        due_date=when,
        payment_date=when,
        concept="Mensualidad",
    )


class TestManualPaymentIsAlwaysPending:
    """QA: "Estado" is gone from payment creation — a created payment is pending."""

    def test_completed_status_is_ignored(self, authenticated_client, student_with_parent, site_config):
        parent = student_with_parent.parents.first()
        authenticated_client.post(
            reverse("create_payment"),
            data={
                "student_id": student_with_parent.id,
                "parent_id": parent.id,
                "payment_type": "monthly",
                "payment_method": "transfer",
                "amount": "54.00",
                "due_date": date.today().isoformat(),
                # A stale/crafted form trying to book it as already collected:
                "payment_status": "completed",
                "payment_date": date.today().isoformat(),
                "concept": "Mensualidad",
            },
        )
        payment = Payment.objects.get(student=student_with_parent, concept="Mensualidad")
        assert payment.payment_status == "pending"
        assert payment.payment_date is None


class TestWaitingListRemoval:
    def test_remove_deletes_the_entry(self, authenticated_client, group):
        waiting = Student.objects.create(
            first_name="Espera", is_waiting=True, active=True, group=group, waiting_contact_phone="600000000"
        )
        response = authenticated_client.post(reverse("remove_from_waiting_list", args=[waiting.id]))
        assert response.status_code == 302
        assert not Student.objects.filter(id=waiting.id).exists()

    def test_remove_refuses_a_non_waiting_student(self, authenticated_client, student):
        # `student` is a real (non-waiting) student — the endpoint only removes
        # waiting-list entries, so it must 404 rather than delete a real ficha.
        response = authenticated_client.post(reverse("remove_from_waiting_list", args=[student.id]))
        assert response.status_code == 404
        assert Student.objects.filter(id=student.id).exists()


class TestBulkReenroll:
    def test_reenrolls_and_reactivates_selected_students(
        self, authenticated_client, group, parent, enrollment_type_returning_student, site_config
    ):
        # A prior, now-inactive student with a parent on file.
        old = Student.objects.create(
            first_name="Antiguo", last_name="Alumno", active=False, is_waiting=False, group=group
        )
        old.parents.add(parent)

        response = authenticated_client.post(
            reverse("reenroll_old_students"),
            data={
                "student_ids": [old.id],
                "enrollment_plan": "monthly_full",
                "start_date": date.today().isoformat(),
                "charge_enrollment_fee": "on",
            },
        )
        assert response.status_code == 302
        old.refresh_from_db()
        assert old.active is True
        enrollment = Enrollment.objects.get(student=old, status="active")
        # Treated as a returning student.
        assert enrollment.enrollment_type.name == "returning_student"
        # Matrícula charged + at least the first period.
        assert Payment.objects.filter(student=old, payment_type="enrollment").exists()
        assert Payment.objects.filter(student=old, payment_type="monthly").exists()

    def test_candidate_list_excludes_already_enrolled(self, authenticated_client, student, active_enrollment):
        # A student already enrolled this year is not a re-enrollment candidate.
        response = authenticated_client.get(reverse("reenroll_old_students"))
        assert student.id not in {s.id for s in response.context["candidates"]}


class TestReceiptNumbering:
    def test_2026_continues_the_paper_sequence(self, student_with_parent, active_enrollment, site_config):
        parent = student_with_parent.parents.first()
        p1 = _completed_payment(student_with_parent, parent, active_enrollment, date(2026, 9, 3))
        assert p1.assign_receipt_number() == "2026-633"
        # Idempotent — a second call keeps the same number.
        assert p1.assign_receipt_number() == "2026-633"
        p2 = _completed_payment(student_with_parent, parent, active_enrollment, date(2026, 10, 1))
        assert p2.assign_receipt_number() == "2026-634"

    def test_january_restarts_per_year(self, student_with_parent, active_enrollment, site_config):
        parent = student_with_parent.parents.first()
        p = _completed_payment(student_with_parent, parent, active_enrollment, date(2027, 1, 10))
        assert p.assign_receipt_number() == "2027-001"


class TestReceiptBreakdown:
    def test_precio_menos_descuentos_equals_importe(
        self, student_with_parent, site_config, enrollment_type_new_student
    ):
        from billing.money import period_base_amount, round_money
        from billing.services.pdf_service import _receipt_breakdown_rows

        base = round_money(period_base_amount(site_config, "full_time", "monthly"))
        sibling = round_money(base * (Decimal(site_config.sibling_discount) / Decimal("100")))
        amount = round_money(base - sibling)

        enrollment = Enrollment.objects.create(
            student=student_with_parent,
            enrollment_type=enrollment_type_new_student,
            enrollment_period_start=date(2026, 9, 15),
            enrollment_period_end=date(2027, 6, 27),
            academic_year="2026-2027",
            schedule_type="full_time",
            payment_modality="monthly",
            is_sibling_discount=True,
            enrollment_amount=base,
            discount_percentage=Decimal("0.00"),
            final_amount=amount,
            status="active",
            enrollment_date=date(2026, 9, 1),
        )
        payment = Payment.objects.create(
            student=student_with_parent,
            parent=student_with_parent.parents.first(),
            enrollment=enrollment,
            payment_type="monthly",
            payment_method="transfer",
            amount=amount,
            payment_status="completed",
            due_date=date(2026, 9, 30),
            payment_date=date(2026, 9, 30),
            concept="Mensualidad",
        )
        rows = _receipt_breakdown_rows(payment)
        labels = [r[0] for r in rows]
        assert "Precio base" in labels
        assert any("Descuento hermano" in label for label in labels)
        # No leftover residual line — the discounts fully explain the importe.
        # `labels`, not `rows[0]`: iterating the first ROW scanned the two cells
        # of "Precio base" and could never see a residual row at all, so this
        # assertion passed no matter what the breakdown emitted.
        assert not any("Prorrateo" in label or "Ajuste" in label for label in labels)
        # And the column adds up: base minus every discount equals the importe.
        # Discount cells already carry the U+2212 minus sign, so they sum in.
        shown = [Decimal(r[1].replace("−", "-").replace(" €", "").strip()) for r in rows]
        assert sum(shown) == amount


class TestReceiptBreakdownReconstructsOrSaysNothing:
    """The breakdown is derived from the SAME call that priced the payment, and
    is shown only when re-pricing reproduces the billed amount exactly.

    It used to re-derive the discounts in the PDF layer and print whatever was
    left over as "Prorrateo primer periodo" / "Ajuste", so any disagreement
    between the receipt's copy of the rules and the generator's was silently
    relabelled as proration on a document families keep for tax purposes.
    """

    def _quarterly_enrollment(self, student, enrollment_type, *, start):
        from billing.models import SiteConfiguration
        from billing.services.payment_service import PaymentService

        enrollment = Enrollment.objects.create(
            student=student,
            enrollment_type=enrollment_type,
            enrollment_period_start=date(2026, 9, 1),
            enrollment_period_end=date(2027, 6, 30),
            academic_year="2026-2027",
            schedule_type="full_time",
            payment_modality="quarterly",
            enrollment_amount=Decimal("153.90"),
            discount_percentage=Decimal("0.00"),
            final_amount=Decimal("153.90"),
            status="active",
            enrollment_date=start,
        )
        return enrollment, SiteConfiguration.get_config(), PaymentService

    def test_june_stub_is_not_priced_as_a_full_quarter(
        self, student_with_parent, enrollment_type_new_student, site_config
    ):
        """A quarterly plan's last block is a ONE-month June stub. The old
        breakdown priced every quarterly payment as a full quarter with the
        language cheque x3, so the ~2/3 difference printed as a large
        "Prorrateo primer periodo" on a payment that is neither first nor
        prorated."""
        from billing.services.pdf_service import _receipt_breakdown_rows

        enrollment, config, PaymentService = self._quarterly_enrollment(
            student_with_parent, enrollment_type_new_student, start=date(2026, 9, 1)
        )
        periods = PaymentService.billing_periods(enrollment)
        stub = periods[-1]
        assert [m for m, _ in stub["months"]] == [6], "last quarterly block should be the June stub"

        amount = PaymentService.calculate_period_amount(
            enrollment, config, [m for m, _ in stub["months"]], stub["fraction"], quarterly=True
        )
        payment = Payment.objects.create(
            student=student_with_parent,
            parent=student_with_parent.parents.first(),
            enrollment=enrollment,
            payment_type="quarterly",
            payment_method="transfer",
            amount=amount,
            payment_status="completed",
            due_date=stub["due"],
            payment_date=stub["due"],
            concept="Trimestre Junio 2027",
        )

        rows = _receipt_breakdown_rows(payment)
        labels = [r[0] for r in rows]
        assert not any("Prorrateo" in label or "Ajuste" in label for label in labels)
        shown = [Decimal(r[1].replace("−", "-").replace(" €", "").strip()) for r in rows]
        assert sum(shown) == amount

    def test_breakdown_is_withheld_when_prices_changed_since(
        self, student_with_parent, enrollment_type_new_student, site_config
    ):
        """Re-downloading an old receipt after a price rise must not restate its
        base at the NEW price and call the difference an "Ajuste"."""
        from billing.services.pdf_service import _receipt_breakdown_rows

        enrollment, config, PaymentService = self._quarterly_enrollment(
            student_with_parent, enrollment_type_new_student, start=date(2026, 9, 1)
        )
        period = PaymentService.billing_periods(enrollment)[0]
        amount = PaymentService.calculate_period_amount(
            enrollment, config, [m for m, _ in period["months"]], period["fraction"], quarterly=True
        )
        payment = Payment.objects.create(
            student=student_with_parent,
            parent=student_with_parent.parents.first(),
            enrollment=enrollment,
            payment_type="quarterly",
            payment_method="transfer",
            amount=amount,
            payment_status="completed",
            due_date=period["due"],
            payment_date=period["due"],
            concept="Trimestre Septiembre-Noviembre 2026",
        )
        assert _receipt_breakdown_rows(payment), "sanity: the breakdown reconstructs before the price change"

        site_config.full_time_monthly_fee = site_config.full_time_monthly_fee + Decimal("4.00")
        site_config.save()

        assert _receipt_breakdown_rows(payment) == []

    def test_special_matricula_below_standard_is_not_a_returning_discount(
        self, student_with_parent, enrollment_type_special, site_config
    ):
        """A negotiated matrícula cheaper than the standard fee used to print
        "Descuento antiguo alumno" for a family with no prior history at all."""
        from billing.services.pdf_service import _receipt_breakdown_rows

        enrollment = Enrollment.objects.create(
            student=student_with_parent,
            enrollment_type=enrollment_type_special,
            enrollment_period_start=date(2026, 9, 1),
            enrollment_period_end=date(2027, 6, 30),
            academic_year="2026-2027",
            schedule_type="full_time",
            payment_modality="monthly",
            enrollment_amount=Decimal("54.00"),
            discount_percentage=Decimal("0.00"),
            final_amount=Decimal("54.00"),
            status="active",
            enrollment_date=date(2026, 9, 1),
        )
        negotiated = Decimal(site_config.children_enrollment_fee) - Decimal("15.00")
        payment = Payment.objects.create(
            student=student_with_parent,
            parent=student_with_parent.parents.first(),
            enrollment=enrollment,
            payment_type="enrollment",
            payment_method="transfer",
            amount=negotiated,
            payment_status="completed",
            due_date=date(2026, 9, 30),
            payment_date=date(2026, 9, 30),
            concept="Matrícula",
        )

        labels = [r[0] for r in _receipt_breakdown_rows(payment)]
        assert not any("antiguo alumno" in label for label in labels)


class TestReceiptNumberIsOnlyForCollectedMoney:
    def test_pending_payment_gets_no_number(self, pending_payment):
        """`assign_receipt_number` is a WRITE — the fiscal sequence continues the
        academy's paper books, so an uncollected charge must not consume one."""
        assert pending_payment.assign_receipt_number() == ""
        pending_payment.refresh_from_db()
        assert pending_payment.receipt_number == ""

    def test_completed_payment_gets_a_number_once(self, completed_payment):
        first = completed_payment.assign_receipt_number()
        assert first
        assert completed_payment.assign_receipt_number() == first

    def test_sequence_crosses_999_correctly(self, student_with_parent, active_enrollment, site_config):
        """The next number is found by ordering, not by a SQL cast — so it must
        order NUMERICALLY across a digit-width change. A plain lexicographic
        `max` would pick "2027-999" over "2027-1000" and re-issue 1000 forever,
        colliding on `unique_receipt_number`."""
        parent = student_with_parent.parents.first()
        earlier = _completed_payment(student_with_parent, parent, active_enrollment, date(2027, 3, 1))
        earlier.receipt_number = "2027-999"
        earlier.save(update_fields=["receipt_number"])

        nxt = _completed_payment(student_with_parent, parent, active_enrollment, date(2027, 4, 1))
        assert nxt.assign_receipt_number() == "2027-1000"

        after = _completed_payment(student_with_parent, parent, active_enrollment, date(2027, 5, 1))
        assert after.assign_receipt_number() == "2027-1001"

    def test_malformed_receipt_number_does_not_break_numbering(
        self, student_with_parent, active_enrollment, site_config
    ):
        """A hand-edited value must not stop the academy issuing receipts. This
        is why the suffix is never cast to an integer in SQL: a cast in the
        SELECT list is not guaranteed to run after the WHERE that filters such
        rows out, and it would raise inside the numbering lock."""
        parent = student_with_parent.parents.first()
        junk = _completed_payment(student_with_parent, parent, active_enrollment, date(2027, 2, 1))
        junk.receipt_number = "2027-ABC"
        junk.save(update_fields=["receipt_number"])

        nxt = _completed_payment(student_with_parent, parent, active_enrollment, date(2027, 2, 2))
        assert nxt.assign_receipt_number() == "2027-001"
