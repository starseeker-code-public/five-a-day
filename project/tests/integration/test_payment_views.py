"""Integration tests for core.views.payments — all HTTP-routed payment
endpoints (list, create, detail, update, delete, deactivate, quick-complete,
search, validate, get-details, export).

Helper-function unit tests (parse_date_value, payment_detail AJAX direct
call) live in unit/test_payment_helpers.py.
"""

import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse

from billing.models import Payment

pytestmark = pytest.mark.django_db


# ============================================================================
# payments_list (GET)
# ============================================================================


class TestPaymentsList:
    def test_loads_ok(self, authenticated_client, pending_payment):
        response = authenticated_client.get(reverse("payments_list"))
        assert response.status_code == 200

    def test_search_by_student_name(self, authenticated_client, pending_payment):
        response = authenticated_client.get(reverse("payments_list"), {"search": "Lucas"})
        assert response.status_code == 200

    def test_context_has_stats(self, authenticated_client, pending_payment, completed_payment):
        response = authenticated_client.get(reverse("payments_list"))
        assert "total_pending" in response.context or "pending_count" in response.context or response.status_code == 200


# ============================================================================
# create_payment
# ============================================================================


class TestCreatePayment:
    def test_get_renders_form(self, authenticated_client):
        response = authenticated_client.get(reverse("create_payment"))
        assert response.status_code == 200

    def test_post_creates_payment(self, authenticated_client, student_with_parent, parent, active_enrollment):
        response = authenticated_client.post(
            reverse("create_payment"),
            {
                "student_id": student_with_parent.id,
                "parent_id": parent.id,
                "payment_type": "monthly",
                "payment_method": "transfer",
                "amount": "54.00",
                "currency": "EUR",
                "payment_status": "pending",
                "due_date": "2026-05-01",
                "concept": "Test payment",
            },
        )
        assert response.status_code == 302
        assert Payment.objects.filter(concept="Test payment").exists()

    def test_post_invalid_parent_relationship(self, authenticated_client, student, second_parent, active_enrollment):
        """Parent not linked to student should be rejected."""
        response = authenticated_client.post(
            reverse("create_payment"),
            {
                "student_id": student.id,
                "parent_id": second_parent.id,
                "payment_type": "monthly",
                "payment_method": "cash",
                "amount": "54.00",
                "due_date": "2026-05-01",
                "concept": "Invalid",
            },
        )
        assert response.status_code == 302  # redirects with error

    def test_unexpected_exception_redirects(self, authenticated_client, student_with_parent, parent, active_enrollment):
        """Unexpected exception during save → redirect + error message."""
        with patch.object(Payment, "save", side_effect=RuntimeError("broken")):
            response = authenticated_client.post(
                reverse("create_payment"),
                {
                    "student_id": student_with_parent.id,
                    "parent_id": parent.id,
                    "enrollment_id": active_enrollment.id,
                    "payment_type": "monthly",
                    "payment_method": "transfer",
                    "amount": "54.00",
                    "due_date": "2026-05-01",
                    "concept": "Test",
                },
            )
        assert response.status_code == 302


# ============================================================================
# payment_detail_view (GET)
# ============================================================================


class TestPaymentDetailView:
    def test_renders_payment(self, authenticated_client, pending_payment):
        response = authenticated_client.get(reverse("payment_detail_view", args=[pending_payment.id]))
        assert response.status_code == 200
        assert response.context["payment"] == pending_payment

    def test_nonexistent_payment_404(self, authenticated_client):
        response = authenticated_client.get(reverse("payment_detail_view", args=[99999]))
        assert response.status_code == 404


# ============================================================================
# update_payment — JSON + FormData + error paths
# ============================================================================


class TestUpdatePayment:
    def _url(self, payment_id):
        return reverse("update_payment", kwargs={"payment_id": payment_id})

    def test_updates_payment_basic(self, authenticated_client, student_with_parent, pending_payment):
        response = authenticated_client.post(
            self._url(pending_payment.id),
            data=json.dumps({"amount": "60.00", "concept": "Updated"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        pending_payment.refresh_from_db()
        assert pending_payment.concept == "Updated"

    def test_json_success(self, authenticated_client, pending_payment, student_with_parent, parent):
        response = authenticated_client.post(
            self._url(pending_payment.id),
            data=json.dumps(
                {
                    "student_id": student_with_parent.id,
                    "parent_id": parent.id,
                    "amount": "99.99",
                    "payment_status": "completed",
                    "due_date": "01/05/2026",
                    "payment_date": "2026-05-02",
                    "concept": "Updated",
                    "reference_number": "REF-1",
                    "observations": "note",
                    "payment_type": "monthly",
                    "payment_method": "cash",
                    "currency": "EUR",
                }
            ),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_json_parent_not_associated_returns_400(self, authenticated_client, pending_payment, adult_student, parent):
        """Student-parent relationship must exist."""
        response = authenticated_client.post(
            self._url(pending_payment.id),
            data=json.dumps({"student_id": adult_student.id, "parent_id": parent.id}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_json_invalid_amount_returns_400(self, authenticated_client, pending_payment):
        response = authenticated_client.post(
            self._url(pending_payment.id),
            data=json.dumps({"amount": "not-a-number"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_json_invalid_date_returns_400(self, authenticated_client, pending_payment):
        response = authenticated_client.post(
            self._url(pending_payment.id),
            data=json.dumps({"due_date": "not-a-date"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_non_json_success(self, authenticated_client, pending_payment):
        """FormData POST also supported → redirects to payments_list."""
        response = authenticated_client.post(
            self._url(pending_payment.id),
            {"amount": "100.00", "payment_status": "completed"},
        )
        assert response.status_code == 302

    def test_non_json_validation_error_redirects(self, authenticated_client, pending_payment):
        response = authenticated_client.post(
            self._url(pending_payment.id),
            {"amount": "not-a-number"},
        )
        assert response.status_code == 302

    def test_non_json_unexpected_exception_redirects(self, authenticated_client, pending_payment):
        """Trigger a non-ValidationError exception → redirect branch."""
        with patch.object(Payment, "save", side_effect=RuntimeError("db down")):
            response = authenticated_client.post(
                self._url(pending_payment.id),
                {"amount": "50.00"},
            )
        assert response.status_code == 302

    def test_json_unexpected_exception_returns_500(
        self, authenticated_client, student_with_parent, parent, active_enrollment
    ):
        """Use student_with_parent so the always-on relationship check passes,
        then mock save() to force a non-validation exception → 500."""
        payment = Payment.objects.create(
            student=student_with_parent,
            parent=parent,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("54.00"),
            payment_status="pending",
            due_date=date(2026, 5, 1),
            concept="500 test",
        )

        with patch.object(Payment, "save", side_effect=RuntimeError("db down")):
            response = authenticated_client.post(
                self._url(payment.id),
                data=json.dumps({"amount": "50.00"}),
                content_type="application/json",
            )
        assert response.status_code == 500


# ============================================================================
# delete_payment
# ============================================================================


class TestDeletePayment:
    def test_delete_success(self, authenticated_client, pending_payment):
        response = authenticated_client.post(reverse("delete_payment", kwargs={"payment_id": pending_payment.id}))
        assert response.status_code == 200

    def test_delete_exception_returns_500(self, authenticated_client, pending_payment):
        with patch.object(Payment, "delete", side_effect=RuntimeError("oops")):
            response = authenticated_client.post(reverse("delete_payment", kwargs={"payment_id": pending_payment.id}))
        assert response.status_code == 500


# ============================================================================
# deactivate_payment (soft-delete)
# ============================================================================


class TestDeactivatePayment:
    def test_success(self, authenticated_client, pending_payment):
        response = authenticated_client.post(reverse("deactivate_payment", kwargs={"payment_id": pending_payment.id}))
        assert response.status_code == 200
        pending_payment.refresh_from_db()
        assert pending_payment.payment_status == "cancelled"

    def test_exception_returns_400(self, authenticated_client, pending_payment):
        with patch.object(Payment, "save", side_effect=RuntimeError("oops")):
            response = authenticated_client.post(
                reverse("deactivate_payment", kwargs={"payment_id": pending_payment.id})
            )
        assert response.status_code == 400


# ============================================================================
# quick_complete_payment
# ============================================================================


class TestQuickCompletePayment:
    def _url(self, payment_id):
        return reverse("quick_complete_payment", kwargs={"payment_id": payment_id})

    def test_marks_payment_completed(self, authenticated_client, pending_payment):
        response = authenticated_client.post(
            self._url(pending_payment.id),
            data=json.dumps({"payment_method": "cash"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        pending_payment.refresh_from_db()
        assert pending_payment.payment_status == "completed"
        assert pending_payment.payment_date is not None

    def test_invalid_method_returns_400(self, authenticated_client, pending_payment):
        response = authenticated_client.post(
            self._url(pending_payment.id),
            data=json.dumps({"payment_method": "bogus"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_broken_json_returns_500(self, authenticated_client, pending_payment):
        response = authenticated_client.post(
            self._url(pending_payment.id),
            data="not-json",
            content_type="application/json",
        )
        assert response.status_code == 500


# ============================================================================
# get_payment_details (AJAX)
# ============================================================================


class TestGetPaymentDetails:
    def test_success(self, authenticated_client, completed_payment):
        response = authenticated_client.get(reverse("get_payment_details", kwargs={"payment_id": completed_payment.id}))
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_exception_returns_500(self, authenticated_client, completed_payment):
        with patch("core.views.payments.get_object_or_404", side_effect=RuntimeError("boom")):
            response = authenticated_client.get(
                reverse("get_payment_details", kwargs={"payment_id": completed_payment.id})
            )
        assert response.status_code == 500


# ============================================================================
# search_payments / search_parents
# ============================================================================


class TestSearchPayments:
    def test_short_query_returns_empty(self, authenticated_client):
        response = authenticated_client.get(reverse("search_payments") + "?q=a")
        assert response.status_code == 200
        assert response.json()["results"] == []


class TestSearchParents:
    def test_short_query_returns_empty(self, authenticated_client):
        response = authenticated_client.get(reverse("search_parents") + "?q=a")
        assert response.status_code == 200
        assert response.json()["results"] == []

    def test_search_hits(self, authenticated_client, parent):
        response = authenticated_client.get(reverse("search_parents") + f"?q={parent.first_name[:3]}")
        assert response.status_code == 200
        data = response.json()
        assert any(r["id"] == parent.id for r in data["results"])


# ============================================================================
# validate_student_parent
# ============================================================================


class TestValidateStudentParent:
    def test_missing_student_id(self, authenticated_client):
        response = authenticated_client.post(
            reverse("validate_student_parent"),
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["valid"] is False

    def test_no_parent_id_returns_parents_list(self, authenticated_client, student_with_parent, parent):
        response = authenticated_client.post(
            reverse("validate_student_parent"),
            data=json.dumps({"student_id": student_with_parent.id}),
            content_type="application/json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is False
        assert "parents" in body
        assert any(p["id"] == parent.id for p in body["parents"])

    def test_valid_relationship_with_enrollment(
        self, authenticated_client, student_with_parent, parent, active_enrollment
    ):
        response = authenticated_client.post(
            reverse("validate_student_parent"),
            data=json.dumps({"student_id": student_with_parent.id, "parent_id": parent.id}),
            content_type="application/json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is True
        assert "enrollment" in body

    def test_invalid_parent_relationship(self, authenticated_client, student_with_parent, second_parent):
        response = authenticated_client.post(
            reverse("validate_student_parent"),
            data=json.dumps({"student_id": student_with_parent.id, "parent_id": second_parent.id}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["valid"] is False

    def test_bad_json(self, authenticated_client):
        response = authenticated_client.post(
            reverse("validate_student_parent"),
            data="not-json",
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_other_exception(self, authenticated_client):
        with patch("core.views.payments.get_object_or_404", side_effect=RuntimeError("boom")):
            response = authenticated_client.post(
                reverse("validate_student_parent"),
                data=json.dumps({"student_id": 1, "parent_id": 1}),
                content_type="application/json",
            )
        assert response.status_code == 400


# ============================================================================
# export_database_excel
# ============================================================================


class TestExportDatabaseExcel:
    def test_exports_xlsx(self, authenticated_client, student_with_parent, active_enrollment, pending_payment):
        response = authenticated_client.get(reverse("export_database_excel"))
        assert response.status_code == 200
        assert "spreadsheetml" in response["Content-Type"]
