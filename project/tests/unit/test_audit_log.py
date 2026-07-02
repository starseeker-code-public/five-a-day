"""Unit tests for the audit log signal + model (v1.10)."""

from decimal import Decimal

import pytest

from core.models import AuditLog

pytestmark = pytest.mark.django_db


class TestAuditLogSignals:
    def test_create_is_logged(self, group):
        from datetime import date

        from students.models import Student

        before = AuditLog.objects.filter(model="students.Student", action="create").count()
        Student.objects.create(
            first_name="Audited",
            last_name="One",
            birth_date=date(2018, 1, 1),
            gdpr_signed=True,
            group=group,
        )
        after = AuditLog.objects.filter(model="students.Student", action="create").count()
        assert after == before + 1

    def test_update_records_diff(self, student):
        student.first_name = "Updated"
        student.save()
        log = AuditLog.objects.filter(model="students.Student", action="update").latest("id")
        assert "first_name" in log.changes

    def test_update_diff_scoped_to_changed_fields_only(self, student):
        AuditLog.objects.all().delete()
        student.first_name = "OnlyThis"
        student.save()
        log = AuditLog.objects.filter(model="students.Student", action="update").latest("id")
        # first_name is definitely in the diff; last_name should not be
        assert "first_name" in log.changes
        assert "last_name" not in log.changes

    def test_delete_is_logged(self, group, teacher):
        from students.models import Group

        g = Group.objects.create(group_name="Ephemeral", color="#000", teacher=teacher)
        gid = g.id
        g.delete()
        assert AuditLog.objects.filter(model="students.Group", object_id=str(gid), action="delete").exists()

    def test_payment_update_recorded(self, pending_payment):
        pending_payment.amount = Decimal("99.99")
        pending_payment.save()
        log = AuditLog.objects.filter(model="billing.Payment", action="update").latest("id")
        assert "amount" in log.changes


class TestAuditLogRecord:
    def test_record_populates_object_label(self, student):
        log = AuditLog.record(action="update", instance=student, changes={"x": [1, 2]})
        assert log.object_label == str(student)[:300]
        assert log.model == "students.Student"
        assert log.object_id == str(student.pk)
