"""
Tests for Teacher <-> auth.User synchronization.

Covers:
  - Teacher.ensure_user() creates a linked User on first call
  - Second call is idempotent and syncs fields
  - The post_save signal mirrors admin/email/name changes onto the linked User
"""

import pytest
from django.contrib.auth import get_user_model

from students.models import Teacher

User = get_user_model()

pytestmark = pytest.mark.django_db


class TestEnsureUser:
    def test_creates_linked_user_on_first_call(self):
        teacher = Teacher.objects.create(
            first_name="Ana",
            last_name="García",
            email="ana.new@example.com",
            admin=False,
        )
        assert teacher.user_id is None

        user = teacher.ensure_user(password="initial-pw")
        teacher.refresh_from_db()

        assert teacher.user_id == user.pk
        assert user.username == "ana.new@example.com"
        assert user.email == "ana.new@example.com"
        assert user.first_name == "Ana"
        assert user.last_name == "García"
        assert user.is_staff is False
        assert user.is_superuser is False
        assert user.check_password("initial-pw") is True

    def test_admin_teacher_gets_superuser_flags(self):
        teacher = Teacher.objects.create(
            first_name="Bigboss",
            last_name="Admin",
            email="boss@example.com",
            admin=True,
        )
        user = teacher.ensure_user(password="pw")
        assert user.is_staff is True
        assert user.is_superuser is True

    def test_second_call_is_idempotent(self):
        teacher = Teacher.objects.create(
            first_name="Carla",
            last_name="Iglesias",
            email="carla@example.com",
            admin=False,
        )
        user_a = teacher.ensure_user(password="pw1")
        user_b = teacher.ensure_user()  # no password change
        assert user_a.pk == user_b.pk
        # Password unchanged (ensure_user() with None doesn't reset it)
        user_b.refresh_from_db()
        assert user_b.check_password("pw1") is True

    def test_no_password_gives_unusable_password(self):
        teacher = Teacher.objects.create(
            first_name="Diana",
            last_name="Soler",
            email="diana@example.com",
            admin=False,
        )
        user = teacher.ensure_user()  # no password
        assert user.has_usable_password() is False

    def test_name_and_email_changes_propagate(self):
        teacher = Teacher.objects.create(
            first_name="Elena",
            last_name="Ruiz",
            email="elena@example.com",
        )
        teacher.ensure_user(password="pw")

        teacher.first_name = "ElenaBis"
        teacher.email = "elena.bis@example.com"
        teacher.save()  # triggers post_save signal

        teacher.refresh_from_db()
        teacher.user.refresh_from_db()
        assert teacher.user.first_name == "ElenaBis"
        assert teacher.user.email == "elena.bis@example.com"
        assert teacher.user.username == "elena.bis@example.com"

    def test_admin_flag_toggle_propagates(self):
        teacher = Teacher.objects.create(
            first_name="Fiona",
            last_name="Prieto",
            email="fiona@example.com",
            admin=False,
        )
        teacher.ensure_user(password="pw")
        assert teacher.user.is_staff is False

        teacher.admin = True
        teacher.save()
        teacher.user.refresh_from_db()
        assert teacher.user.is_staff is True
        assert teacher.user.is_superuser is True

    def test_signal_noop_when_no_linked_user(self):
        """Teacher without linked User: saving should not explode."""
        teacher = Teacher.objects.create(
            first_name="Gloria",
            last_name="Pérez",
            email="gloria@example.com",
        )
        teacher.admin = True
        teacher.save()  # should be a harmless no-op on the User side
        assert teacher.user_id is None

    def test_ensure_user_updates_password_on_linked_user(self):
        """Passing a password when the user is already linked rotates it."""
        teacher = Teacher.objects.create(
            first_name="Rotate",
            last_name="Pw",
            email="rotate@example.com",
        )
        teacher.ensure_user(password="first-pw")
        teacher.ensure_user(password="second-pw")
        teacher.user.refresh_from_db()
        assert teacher.user.check_password("second-pw") is True
        assert teacher.user.check_password("first-pw") is False

    def test_ensure_user_syncs_all_fields_on_linked_user(self):
        """Mutating teacher fields then re-calling ensure_user updates the linked user."""
        teacher = Teacher.objects.create(
            first_name="OldFirst",
            last_name="OldLast",
            email="rename@example.com",
            admin=False,
        )
        teacher.ensure_user(password="pw")

        # Mutate the Teacher in-memory only (don't trigger signal).
        teacher.first_name = "NewFirst"
        teacher.last_name = "NewLast"
        teacher.email = "renamed@example.com"
        teacher.admin = True
        teacher.ensure_user()  # exercises the "already linked" sync branch

        teacher.user.refresh_from_db()
        assert teacher.user.first_name == "NewFirst"
        assert teacher.user.last_name == "NewLast"
        assert teacher.user.email == "renamed@example.com"
        assert teacher.user.username == "renamed@example.com"
        assert teacher.user.is_staff is True
        assert teacher.user.is_superuser is True

    def test_ensure_user_links_existing_user_by_username(self):
        """If a User already exists with the teacher's email as username, link it."""
        existing = User.objects.create_user(
            username="helena@example.com",
            email="helena@example.com",
            password="old-password",
        )
        teacher = Teacher.objects.create(
            first_name="Helena",
            last_name="Mora",
            email="helena@example.com",
            admin=True,
        )
        linked = teacher.ensure_user()
        assert linked.pk == existing.pk
        teacher.refresh_from_db()
        assert teacher.user_id == existing.pk
        # The existing password is preserved (no password arg was passed)
        linked.refresh_from_db()
        assert linked.check_password("old-password") is True
