"""Spanish choice labels, the EnrollmentType mirror, and fields nothing writes.

Choice labels reach the family through `get_<field>_display()`, so an English
one lands in the middle of a Spanish UI. The rest is dead weight: discount
columns no write path sets, and a Drive button that must stay hidden.

From the iteration-2 review."""

from decimal import Decimal

import pytest
from django.urls import reverse

from billing.constants import ENROLLMENT_TYPE_CHOICES
from billing.models import EnrollmentType
from core.audit_models import AuditLog
from core.models import BacklogTask

pytestmark = pytest.mark.django_db


class TestSpanishChoiceLabels:
    """#79 — choice labels rendered by get_*_display() are Spanish."""

    def test_backlog_task_labels(self):
        assert dict(BacklogTask.PRIORITY_CHOICES)["high"] == "Alta"
        assert dict(BacklogTask.STATUS_CHOICES)["in_progress"] == "En progreso"

    def test_audit_action_labels(self):
        assert dict(AuditLog.ACTION_CHOICES)["create"] == "Creación"

    def test_enrollment_type_labels(self):
        assert dict(ENROLLMENT_TYPE_CHOICES)["new_student"] == "Nuevo estudiante"


class TestEnrollmentTypeMirrorReDerived:
    """#77 — a price edit re-derives the EnrollmentType.base_amount_* mirror."""

    def test_config_update_refreshes_mirror(self, authenticated_client, site_config, enrollment_type_new_student):
        response = authenticated_client.post(
            reverse("update_site_config"),
            data='{"children_enrollment_fee": "99.00"}',
            content_type="application/json",
        )
        assert response.status_code == 200
        et = EnrollmentType.objects.get(name="new_student")
        assert et.base_amount_full_time == Decimal("99.00")


class TestDeadDiscountFieldsNotWritten:
    """#14 — the five inert discount fields were never persisted, and as of
    v1.29.5 the COLUMNS are gone (`billing/0017`).

    The original assertion (post the field, read it back unchanged) can no
    longer be written, because there is nothing to read back. What still has to
    hold is the property that made the exclusion matter in the first place: an
    unknown key in the payload must be IGNORED, not crash the endpoint and not
    reach the model. `old_student_discount` is the one worth naming — it is
    visually the twin of the LIVE `returning_student_enrollment_discount`, so a
    payload carrying it must not touch the price that actually applies.
    """

    def test_a_removed_discount_field_is_ignored_not_an_error(self, authenticated_client, site_config):
        live_before = site_config.returning_student_enrollment_discount

        response = authenticated_client.post(
            reverse("update_site_config"),
            data='{"old_student_discount": "77.00"}',
            content_type="application/json",
        )

        assert response.status_code == 200
        assert not hasattr(site_config, "old_student_discount"), "column dropped in billing/0017"
        site_config.refresh_from_db()
        # And it did not land on its look-alike.
        assert site_config.returning_student_enrollment_discount == live_before


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
