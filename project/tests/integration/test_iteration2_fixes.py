"""Regression tests for the iteration-2 review fixes (dead code, dedup, i18n, infra)."""

from decimal import Decimal

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


class TestSpanishChoiceLabels:
    """#79 — choice labels rendered by get_*_display() are Spanish."""

    def test_backlog_task_labels(self):
        from core.models import BacklogTask

        assert dict(BacklogTask.PRIORITY_CHOICES)["high"] == "Alta"
        assert dict(BacklogTask.STATUS_CHOICES)["in_progress"] == "En progreso"

    def test_audit_action_labels(self):
        from core.audit_models import AuditLog

        assert dict(AuditLog.ACTION_CHOICES)["create"] == "Creación"

    def test_enrollment_type_labels(self):
        from billing.constants import ENROLLMENT_TYPE_CHOICES

        assert dict(ENROLLMENT_TYPE_CHOICES)["new_student"] == "Nuevo estudiante"


class TestEnrollmentTypeMirrorReDerived:
    """#77 — a price edit re-derives the EnrollmentType.base_amount_* mirror."""

    def test_config_update_refreshes_mirror(self, authenticated_client, site_config, enrollment_type_new_student):
        from billing.models import EnrollmentType

        response = authenticated_client.post(
            reverse("update_site_config"),
            data='{"children_enrollment_fee": "99.00"}',
            content_type="application/json",
        )
        assert response.status_code == 200
        et = EnrollmentType.objects.get(name="new_student")
        assert et.base_amount_full_time == Decimal("99.00")


class TestDeadDiscountFieldsNotWritten:
    """#14 — the five inert discount fields are no longer persisted."""

    def test_old_student_discount_ignored(self, authenticated_client, site_config):
        before = site_config.old_student_discount
        response = authenticated_client.post(
            reverse("update_site_config"),
            data='{"old_student_discount": "77.00"}',
            content_type="application/json",
        )
        assert response.status_code == 200
        site_config.refresh_from_db()
        assert site_config.old_student_discount == before  # unchanged


class TestDriveButtonHidden:
    """#74 — the receipts button is hidden unless GOOGLE_DRIVE_RECEIPTS_URL is set."""

    def test_hidden_when_unset(self, authenticated_client, settings):
        settings.GOOGLE_DRIVE_RECEIPTS_URL = ""
        response = authenticated_client.get(reverse("expenses_list"))
        assert response.status_code == 200
        assert b"Consultar recibos" not in response.content

    def test_shown_when_set(self, authenticated_client, settings):
        settings.GOOGLE_DRIVE_RECEIPTS_URL = "https://drive.google.com/drive/folders/abc"
        response = authenticated_client.get(reverse("expenses_list"))
        assert response.status_code == 200
        assert b"Consultar recibos" in response.content
