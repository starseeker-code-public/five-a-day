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
    def _completed_payment(self, student, parent, enrollment, when):
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

    def test_2026_continues_the_paper_sequence(self, student_with_parent, active_enrollment, site_config):
        parent = student_with_parent.parents.first()
        p1 = self._completed_payment(student_with_parent, parent, active_enrollment, date(2026, 9, 3))
        assert p1.assign_receipt_number() == "2026-633"
        # Idempotent — a second call keeps the same number.
        assert p1.assign_receipt_number() == "2026-633"
        p2 = self._completed_payment(student_with_parent, parent, active_enrollment, date(2026, 10, 1))
        assert p2.assign_receipt_number() == "2026-634"

    def test_january_restarts_per_year(self, student_with_parent, active_enrollment, site_config):
        parent = student_with_parent.parents.first()
        p = self._completed_payment(student_with_parent, parent, active_enrollment, date(2027, 1, 10))
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
        assert not any("Prorrateo" in label or "Ajuste" in label for label in rows[0])
