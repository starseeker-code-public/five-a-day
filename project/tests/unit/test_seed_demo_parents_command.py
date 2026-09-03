"""
Tests for the `seed_demo_parents` management command and the DEMO_PARENT_<N>_*
env contract it shares with the parent portal's demo login.
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from billing.models import Payment
from core.management.commands.seed_demo_parents import iter_demo_parent_specs
from students.models import Group, Parent, Student, Teacher

pytestmark = pytest.mark.django_db


def _call():
    out = StringIO()
    call_command("seed_demo_parents", stdout=out)
    return out.getvalue()


@pytest.fixture(autouse=True)
def _clear_demo_env(monkeypatch):
    """The real .env is loaded into the process, so every test starts from a
    known-empty contract rather than whatever the developer's file happens to
    hold."""
    for index in range(1, 4):
        for suffix in (
            "USERNAME",
            "PASSWORD",
            "EMAIL",
            "FIRST_NAME",
            "LAST_NAME",
            "DNI",
            "PHONE",
            "IBAN",
            "CHILDREN",
        ):
            monkeypatch.delenv(f"DEMO_PARENT_{index}_{suffix}", raising=False)


def _configure(monkeypatch, index=1, **overrides):
    values = {
        "USERNAME": "parent",
        "PASSWORD": "parent-pw",
        "EMAIL": "demo.parent@example.com",
        "CHILDREN": "Mateo,Valeria",
    }
    values.update(overrides)
    for suffix, value in values.items():
        monkeypatch.setenv(f"DEMO_PARENT_{index}_{suffix}", value)


class TestSpecParsing:
    def test_no_env_yields_nothing(self):
        assert list(iter_demo_parent_specs()) == []

    def test_block_without_password_is_skipped(self, monkeypatch):
        # A username with no password is not a usable credential, and seeding
        # the data without one would create a family nobody can log in as.
        _configure(monkeypatch, PASSWORD="")
        assert list(iter_demo_parent_specs()) == []

    def test_block_without_email_is_skipped(self, monkeypatch):
        # EMAIL is the only link between the credential and the Parent row.
        _configure(monkeypatch, EMAIL="")
        assert list(iter_demo_parent_specs()) == []

    def test_iteration_stops_at_first_gap(self, monkeypatch):
        _configure(monkeypatch, index=1)
        # Block 2 missing entirely; block 3 must therefore never be read.
        _configure(monkeypatch, index=3, USERNAME="third", EMAIL="third@example.com")
        assert [s["username"] for s in iter_demo_parent_specs()] == ["parent"]

    def test_defaults_are_derived_from_username(self, monkeypatch):
        _configure(monkeypatch, FIRST_NAME="", LAST_NAME="", DNI="", PHONE="")
        (spec,) = list(iter_demo_parent_specs())
        assert spec["first_name"] == "Parent"
        assert spec["last_name"] == "Demo"
        assert spec["dni"] == "DEMOPARENT"
        assert spec["phone"] == "600000000"

    def test_children_are_split_and_stripped(self, monkeypatch):
        _configure(monkeypatch, CHILDREN=" Mateo , Valeria ,, ")
        (spec,) = list(iter_demo_parent_specs())
        assert spec["children"] == ["Mateo", "Valeria"]


class TestSeeding:
    def test_no_env_warns_nothing_to_seed(self):
        output = _call()
        assert "nothing to seed" in output.lower()
        assert not Parent.objects.filter(email="demo.parent@example.com").exists()

    def test_creates_parent_with_two_enrolled_siblings(self, monkeypatch):
        _configure(monkeypatch)
        _call()

        parent = Parent.objects.get(email="demo.parent@example.com")
        children = list(parent.children.order_by("first_name"))
        assert [c.first_name for c in children] == ["Mateo", "Valeria"]

        for child in children:
            enrollment = child.enrollments.get()
            assert enrollment.status == "active"
            # Two children under one parent IS the sibling case — if the
            # discount were not applied, the portal would show the wrong money
            # and the whole reason for seeding two children would be lost.
            assert enrollment.is_sibling_discount is True
            # Matrícula + at least the current period.
            assert Payment.objects.filter(student=child, parent=parent).count() >= 2
            assert Payment.objects.filter(student=child, payment_type="enrollment").exists()

    def test_single_child_gets_no_sibling_discount(self, monkeypatch):
        _configure(monkeypatch, CHILDREN="Mateo")
        _call()

        student = Student.objects.get(first_name="Mateo")
        assert student.enrollments.get().is_sibling_discount is False

    def test_is_idempotent(self, monkeypatch):
        _configure(monkeypatch)
        _call()
        output = _call()

        assert Parent.objects.filter(email="demo.parent@example.com").count() == 1
        assert Student.objects.filter(last_name="Demo").count() == 2
        assert "already exists" in output
        # And no second matrícula was charged on the re-run.
        assert Payment.objects.filter(payment_type="enrollment").count() == 2

    def test_reuses_an_existing_active_group(self, monkeypatch, group):
        _configure(monkeypatch)
        _call()

        for student in Student.objects.filter(last_name="Demo"):
            assert student.group_id == group.id

    def test_creates_a_group_when_the_database_has_none(self, monkeypatch):
        # A fresh dev database has no Group, and `Group.teacher` is a non-null
        # PROTECT FK — so the command has to make both or it cannot enroll.
        assert not Group.objects.exists()
        _configure(monkeypatch)
        _call()

        group = Group.objects.get(group_name="Demo")
        assert Teacher.objects.filter(id=group.teacher_id).exists()
        assert Student.objects.filter(last_name="Demo", group=group).count() == 2

    def test_parent_without_children_is_still_created(self, monkeypatch):
        _configure(monkeypatch, CHILDREN="")
        _call()

        parent = Parent.objects.get(email="demo.parent@example.com")
        assert parent.children.count() == 0

    @override_settings(ENVIRONMENT="production")
    def test_refused_in_production(self, monkeypatch):
        _configure(monkeypatch)
        with pytest.raises(CommandError, match="produccion"):
            _call()
        assert not Parent.objects.filter(email="demo.parent@example.com").exists()
