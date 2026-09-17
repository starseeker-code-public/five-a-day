"""What may happen to a Payment after it exists -- and what may not.

One transition table governs every status change; collected money cannot be
voided, a receipt-numbered row cannot be hard-deleted, and only an open charge
can be paid online. The buttons in the UI read the same predicates the
endpoints enforce, so the two cannot disagree.

From the v1.29.2 whole-tree review.

Each class pins one defect the review found:

* the bulk re-enrolment flow reused ONE form across every selected student and
  wrote each student's *effective* start date back onto it, so the next student's
  request started from the previous one's answer;
* collected money could be voided from the payments list (`deactivate_payment`)
  and from the edit endpoint (`update_payment`), while the admin refused it;
* a completed or receipt-numbered payment could be hard-deleted;
* a Stripe checkout link could be minted for a cancelled/refunded charge, which
  the webhook then refuses to reconcile.
"""

import json
import re
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib import admin as dj_admin
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse

from billing.admin import PaymentAdmin
from billing.models import Enrollment, Payment, enrollment_academic_year
from billing.services.payment_service import PaymentService
from billing.services.stripe_service import StripeService
from students.models import Student

pytestmark = pytest.mark.django_db


def _first_open_teaching_month(today):
    """(month, year) of the first teaching month whose last day is on/after today."""
    for month, year in PaymentService.teaching_months(enrollment_academic_year(today)):
        if PaymentService._last_day(month, year) >= today:
            return month, year
    raise AssertionError("no teaching month left in the course")  # pragma: no cover


class TestBulkReenrollStartDateDoesNotDrift:
    def test_each_student_starts_from_the_requested_date(
        self,
        authenticated_client,
        group,
        parent,
        second_parent,
        enrollment_type_new_student,
        enrollment_type_returning_student,
        site_config,
    ):
        today = date.today()
        month, year = _first_open_teaching_month(today)
        covered_due = PaymentService._last_day(month, year)

        # Student A already PAID the first open month, so their transition is
        # pushed to the following month. Student B has nothing billed.
        pushed = Student.objects.create(first_name="Pushed", last_name="Later", active=False, group=group)
        pushed.parents.add(parent)
        Payment.objects.create(
            student=pushed,
            parent=parent,
            payment_type="monthly",
            payment_method="cash",
            amount=Decimal("54.00"),
            payment_status="completed",
            due_date=covered_due,
            payment_date=covered_due,
            concept="Mensualidad ya cobrada",
        )
        clean = Student.objects.create(first_name="Clean", last_name="Slate", active=False, group=group)
        clean.parents.add(second_parent)

        expected_clean_start = PaymentService.transition_start_date(clean, today)
        expected_pushed_start = PaymentService.transition_start_date(pushed, today)
        assert expected_pushed_start != expected_clean_start, "fixture must make the two dates differ"

        response = authenticated_client.post(
            reverse("reenroll_old_students"),
            data={
                # The view processes ids in SORTED order, so `pushed` (created
                # first, lower id) goes before `clean` — the order that used to
                # carry A's effective date onto B.
                "student_ids": [pushed.id, clean.id],
                "enrollment_plan": "monthly_full",
                "start_date": today.isoformat(),
            },
        )
        assert response.status_code == 302

        assert Enrollment.objects.get(student=clean, status="active").enrollment_date == expected_clean_start
        assert Enrollment.objects.get(student=pushed, status="active").enrollment_date == expected_pushed_start


class TestCollectedMoneyCannotBeVoided:
    def test_deactivate_refuses_a_completed_payment(self, authenticated_client, completed_payment):
        response = authenticated_client.post(reverse("deactivate_payment", kwargs={"payment_id": completed_payment.id}))
        assert response.status_code == 400
        assert response.json()["success"] is False
        completed_payment.refresh_from_db()
        assert completed_payment.payment_status == "completed"

    def test_deactivate_still_cancels_a_pending_payment(self, authenticated_client, pending_payment):
        response = authenticated_client.post(reverse("deactivate_payment", kwargs={"payment_id": pending_payment.id}))
        assert response.status_code == 200
        pending_payment.refresh_from_db()
        assert pending_payment.payment_status == "cancelled"

    @pytest.mark.parametrize("voiding_status", ["cancelled", "failed"])
    def test_update_endpoint_refuses_voiding_a_completed_payment(
        self, authenticated_client, student_with_parent, completed_payment, voiding_status
    ):
        response = authenticated_client.post(
            reverse("update_payment", kwargs={"payment_id": completed_payment.id}),
            data=json.dumps({"payment_status": voiding_status}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "ya está cobrado" in response.json()["error"]
        completed_payment.refresh_from_db()
        assert completed_payment.payment_status == "completed"

    def test_model_clean_is_the_single_rule(self, completed_payment):
        completed_payment.payment_status = "cancelled"
        with pytest.raises(ValidationError):
            completed_payment.full_clean()

    def test_payments_list_hides_the_cancel_button_on_collected_rows(
        self, authenticated_client, completed_payment, pending_payment
    ):
        response = authenticated_client.get(reverse("payments_list"), {"year": 2025})
        html = response.content.decode()
        # Every "Cancelar pago" button, by the payment it acts on.
        cancel_ids = {
            int(pid) for pid in re.findall(r'class="payment-cancel-btn.*?data-payment-id="(\d+)"', html, re.S)
        }
        assert pending_payment.id in cancel_ids
        assert completed_payment.id not in cancel_ids


class TestHardDeleteIsForMistakesOnly:
    def test_completed_payment_cannot_be_deleted(self, authenticated_client, completed_payment):
        response = authenticated_client.post(reverse("delete_payment", kwargs={"payment_id": completed_payment.id}))
        assert response.status_code == 400
        assert Payment.objects.filter(pk=completed_payment.pk).exists()

    def test_receipt_numbered_payment_cannot_be_deleted(self, authenticated_client, pending_payment):
        # A number on a pending row is not a state the app produces any more,
        # but a hand-edited one must still be protected: the sequence is fiscal.
        Payment.objects.filter(pk=pending_payment.pk).update(receipt_number="2025-001")
        response = authenticated_client.post(reverse("delete_payment", kwargs={"payment_id": pending_payment.id}))
        assert response.status_code == 400
        assert Payment.objects.filter(pk=pending_payment.pk).exists()


class TestCheckoutLinkOnlyForPayableCharges:
    @pytest.mark.parametrize("dead_status", ["cancelled", "refunded"])
    def test_409_for_a_dead_payment(self, client, pending_payment, dead_status):
        Payment.objects.filter(pk=pending_payment.pk).update(payment_status=dead_status)
        session = client.session
        session["parent_id"] = pending_payment.parent_id
        session.save()
        with override_settings(STRIPE_SECRET_KEY="sk_test_xxx"):
            response = client.post(reverse("stripe_create_checkout_link", args=[pending_payment.id]))
        assert response.status_code == 409


# ============================================================================
# Second pass — the status-transition TABLE and the places that read it
# ============================================================================


class TestStatusTransitionTable:
    """`Payment.assert_transition` is enforced by `save()` for every existing row."""

    def test_plain_save_refuses_voiding_collected_money(self, completed_payment):
        completed_payment.payment_status = "cancelled"
        with pytest.raises(ValidationError):
            completed_payment.save()
        completed_payment.refresh_from_db()
        assert completed_payment.payment_status == "completed"

    def test_refund_only_from_completed(self, pending_payment, completed_payment):
        pending_payment.payment_status = "refunded"
        with pytest.raises(ValidationError):
            pending_payment.save()
        completed_payment.payment_status = "refunded"
        completed_payment.save()  # money that arrived may be returned
        completed_payment.refresh_from_db()
        assert completed_payment.payment_status == "refunded"

    def test_update_endpoint_refuses_refunding_an_uncollected_charge(
        self, authenticated_client, student_with_parent, pending_payment
    ):
        response = authenticated_client.post(
            reverse("update_payment", kwargs={"payment_id": pending_payment.id}),
            data=json.dumps({"payment_status": "refunded"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "reembolsado" in response.json()["error"]
        pending_payment.refresh_from_db()
        assert pending_payment.payment_status == "pending"

    def test_refunded_money_cannot_be_cancelled_either(self, authenticated_client, completed_payment):
        Payment.objects.filter(pk=completed_payment.pk).update(payment_status="refunded")
        response = authenticated_client.post(reverse("deactivate_payment", kwargs={"payment_id": completed_payment.id}))
        assert response.status_code == 400
        completed_payment.refresh_from_db()
        assert completed_payment.payment_status == "refunded"

    def test_update_fields_without_status_costs_no_check(self, completed_payment):
        # A receipt-number write must not re-validate the status (and must not
        # need the row's status to be a legal transition of itself).
        completed_payment.reference_number = "ABC-1"
        completed_payment.save(update_fields=["reference_number"])

    def test_reopening_clears_the_collection_date(self, authenticated_client, student_with_parent, completed_payment):
        response = authenticated_client.post(
            reverse("update_payment", kwargs={"payment_id": completed_payment.id}),
            data=json.dumps({"payment_status": "pending"}),
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        completed_payment.refresh_from_db()
        assert completed_payment.payment_status == "pending"
        assert completed_payment.payment_date is None


class TestUpdatePaymentKeepsTheRowCoherent:
    def test_changing_the_student_re_points_the_enrollment(
        self, authenticated_client, pending_payment, second_parent, group, enrollment_type_new_student, site_config
    ):
        sibling = Student.objects.create(first_name="Hermana", last_name="López", group=group, active=True)
        sibling.parents.add(second_parent)
        year, start_year = pending_payment.enrollment.academic_year, int(pending_payment.enrollment.academic_year[:4])
        sibling_enrollment = Enrollment.objects.create(
            student=sibling,
            enrollment_type=enrollment_type_new_student,
            enrollment_period_start=date(start_year, 9, 15),
            enrollment_period_end=date(start_year + 1, 6, 27),
            academic_year=year,
            enrollment_amount=Decimal("54.00"),
            final_amount=Decimal("54.00"),
            status="active",
            enrollment_date=date(start_year, 9, 1),
        )
        response = authenticated_client.post(
            reverse("update_payment", kwargs={"payment_id": pending_payment.id}),
            data=json.dumps({"student_id": sibling.id, "parent_id": second_parent.id}),
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        pending_payment.refresh_from_db()
        assert pending_payment.student_id == sibling.id
        assert pending_payment.enrollment_id == sibling_enrollment.id

    def test_model_refuses_an_enrollment_of_another_student(self, pending_payment, adult_student):
        pending_payment.student = adult_student
        pending_payment.parent = None
        with pytest.raises(ValidationError):
            pending_payment.full_clean(exclude=["enrollment"])

    def test_numbered_receipt_freezes_amount_and_student(
        self, authenticated_client, student_with_parent, completed_payment
    ):
        Payment.objects.filter(pk=completed_payment.pk).update(receipt_number="2025-010")
        response = authenticated_client.post(
            reverse("update_payment", kwargs={"payment_id": completed_payment.id}),
            data=json.dumps({"amount": "10.00"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "recibo emitido" in response.json()["error"]
        completed_payment.refresh_from_db()
        assert completed_payment.amount == Decimal("54.00")


class TestMalformedJsonIsAClientError:
    @pytest.mark.parametrize("url_name", ["update_payment", "quick_complete_payment"])
    def test_400_not_500(self, authenticated_client, pending_payment, url_name):
        response = authenticated_client.post(
            reverse(url_name, kwargs={"payment_id": pending_payment.id}),
            data="not json at all",
            content_type="application/json",
        )
        assert response.status_code == 400


class _CapturingPaymentAdmin(PaymentAdmin):
    def __init__(self):
        super().__init__(Payment, dj_admin.site)
        self.captured = []

    def message_user(self, request, message, *args, **kwargs):
        self.captured.append(message)


class TestAdminCannotDeleteFiscalRecords:
    def test_row_delete_permission_is_refused(self, completed_payment, pending_payment, rf):
        payment_admin = _CapturingPaymentAdmin()
        request = rf.get("/admin/billing/payment/")
        request.user = type(
            "U", (), {"is_active": True, "is_staff": True, "is_superuser": True, "has_perm": lambda *a: True}
        )()
        assert payment_admin.has_delete_permission(request, completed_payment) is False
        assert payment_admin.has_delete_permission(request, pending_payment) is True

    def test_bulk_delete_skips_protected_rows(self, completed_payment, pending_payment):
        payment_admin = _CapturingPaymentAdmin()
        payment_admin.delete_queryset(None, Payment.objects.filter(pk__in=[completed_payment.pk, pending_payment.pk]))
        assert Payment.objects.filter(pk=completed_payment.pk).exists()
        assert not Payment.objects.filter(pk=pending_payment.pk).exists()
        assert "sin eliminar" in payment_admin.captured[0]

    def test_bulk_void_reads_the_model_rule_for_refunds(self, completed_payment):
        Payment.objects.filter(pk=completed_payment.pk).update(payment_status="refunded")
        payment_admin = _CapturingPaymentAdmin()
        payment_admin.soft_delete_payments(None, Payment.objects.filter(pk=completed_payment.pk))
        completed_payment.refresh_from_db()
        assert completed_payment.payment_status == "refunded"
        assert "sin tocar" in payment_admin.captured[0]


class TestStripeReconciliation:
    def test_paid_session_matched_by_client_reference_id(self, pending_payment):
        # Family clicked "Pagar online" twice: the row carries session B, they paid A.
        Payment.objects.filter(pk=pending_payment.pk).update(stripe_session_id="cs_B")
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_A",
                    "payment_status": "paid",
                    "client_reference_id": str(pending_payment.id),
                    "payment_intent": "pi_1",
                }
            },
        }
        with patch("billing.services.stripe_service.dispatch_payment_completed"):
            result = StripeService().apply_webhook_event(event)
        assert result["status"] == "completed"
        pending_payment.refresh_from_db()
        assert pending_payment.payment_status == "completed"
        assert pending_payment.stripe_session_id == "cs_A"

    def test_unmatched_paid_session_is_logged_as_an_error(self, caplog):
        event = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_ghost", "payment_status": "paid", "client_reference_id": "999999"}},
        }
        with caplog.at_level("ERROR", logger="billing.services.stripe_service"):
            result = StripeService().apply_webhook_event(event)
        assert result["status"] == "ignored"
        assert any("unrecorded" in rec.getMessage() for rec in caplog.records)


class TestIsOpenDrivesTheButtons:
    def test_failed_charge_is_still_payable_and_collectable(self, pending_payment):
        Payment.objects.filter(pk=pending_payment.pk).update(payment_status="failed")
        pending_payment.refresh_from_db()
        assert pending_payment.is_open is True
        assert Payment(payment_status="completed").is_open is False
        assert Payment(payment_status="refunded").is_open is False

    def test_portal_offers_pagar_online_for_a_failed_charge(self, client, pending_payment):
        Payment.objects.filter(pk=pending_payment.pk).update(payment_status="failed")
        session = client.session
        session["parent_id"] = pending_payment.parent_id
        session.save()
        response = client.get(reverse("parent_portal_payments"), {"year": 2025})
        assert "Pagar online" in response.content.decode()


class TestOneStatusBadge:
    def test_database_page_shows_a_refunded_badge(self, authenticated_client, completed_payment):
        Payment.objects.filter(pk=completed_payment.pk).update(payment_status="refunded")
        response = authenticated_client.get(reverse("all_info"))
        assert "Reembolsado" in response.content.decode()
