"""Tests for the `seed_teachers` management command."""

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from students.models import Teacher

User = get_user_model()

pytestmark = pytest.mark.django_db


def _call():
    out = StringIO()
    call_command("seed_teachers", stdout=out)
    return out.getvalue()


@pytest.fixture(autouse=True)
def _clear_teacher_seed_env(monkeypatch):
    """`settings.py` calls `load_dotenv(".env")`, so the developer's own
    TEACHER_SEED_* values are in `os.environ` for the whole test session. Each
    test starts from an empty contract instead of inheriting them — otherwise
    a var a test does not mention (USERNAME, say) silently applies."""
    for index in range(1, 5):
        for suffix in ("FIRST_NAME", "LAST_NAME", "EMAIL", "USERNAME", "PHONE", "ADMIN", "PASSWORD"):
            monkeypatch.delenv(f"TEACHER_SEED_{index}_{suffix}", raising=False)


class TestSeedTeachers:
    def test_empty_env_warns_nothing_to_seed(self, monkeypatch):
        monkeypatch.delenv("TEACHER_SEED_1_FIRST_NAME", raising=False)
        output = _call()
        assert "nothing to seed" in output.lower()

    def test_creates_admin_teacher_and_user(self, monkeypatch):
        monkeypatch.setenv("TEACHER_SEED_1_FIRST_NAME", "Claudia")
        monkeypatch.setenv("TEACHER_SEED_1_LAST_NAME", "Moreno")
        monkeypatch.setenv("TEACHER_SEED_1_EMAIL", "claudia@example.com")
        monkeypatch.setenv("TEACHER_SEED_1_PHONE", "600111222")
        monkeypatch.setenv("TEACHER_SEED_1_ADMIN", "True")
        monkeypatch.setenv("TEACHER_SEED_1_PASSWORD", "secret-pw-123")
        monkeypatch.delenv("TEACHER_SEED_2_FIRST_NAME", raising=False)

        _call()

        teacher = Teacher.objects.get(email="claudia@example.com")
        assert teacher.first_name == "Claudia"
        assert teacher.last_name == "Moreno"
        assert teacher.phone == "600111222"
        assert teacher.admin is True
        assert teacher.user_id is not None
        assert teacher.user.is_staff is True
        assert teacher.user.is_superuser is True
        assert teacher.user.check_password("secret-pw-123") is True

    def test_creates_non_admin_teacher_without_superuser(self, monkeypatch):
        monkeypatch.setenv("TEACHER_SEED_1_FIRST_NAME", "Regular")
        monkeypatch.setenv("TEACHER_SEED_1_LAST_NAME", "Teacher")
        monkeypatch.setenv("TEACHER_SEED_1_EMAIL", "regular@example.com")
        monkeypatch.setenv("TEACHER_SEED_1_ADMIN", "false")
        monkeypatch.setenv("TEACHER_SEED_1_PASSWORD", "pw")
        monkeypatch.delenv("TEACHER_SEED_2_FIRST_NAME", raising=False)

        _call()

        t = Teacher.objects.get(email="regular@example.com")
        assert t.admin is False
        assert t.user.is_staff is False
        assert t.user.is_superuser is False

    def test_idempotent_rerun_does_not_reset_password(self, monkeypatch):
        monkeypatch.setenv("TEACHER_SEED_1_FIRST_NAME", "Irene")
        monkeypatch.setenv("TEACHER_SEED_1_LAST_NAME", "Santos")
        monkeypatch.setenv("TEACHER_SEED_1_EMAIL", "irene@example.com")
        monkeypatch.setenv("TEACHER_SEED_1_ADMIN", "True")
        monkeypatch.setenv("TEACHER_SEED_1_PASSWORD", "initial")
        monkeypatch.delenv("TEACHER_SEED_2_FIRST_NAME", raising=False)

        _call()
        teacher = Teacher.objects.get(email="irene@example.com")
        # Simulate user changing password afterwards
        teacher.user.set_password("user-changed-it")
        teacher.user.save()

        # Re-seed: password must NOT revert to "initial"
        _call()
        teacher.user.refresh_from_db()
        assert teacher.user.check_password("user-changed-it") is True
        assert teacher.user.check_password("initial") is False

    def test_idempotent_rerun_updates_profile_fields(self, monkeypatch):
        monkeypatch.setenv("TEACHER_SEED_1_FIRST_NAME", "Julia")
        monkeypatch.setenv("TEACHER_SEED_1_LAST_NAME", "Old")
        monkeypatch.setenv("TEACHER_SEED_1_EMAIL", "julia@example.com")
        monkeypatch.setenv("TEACHER_SEED_1_ADMIN", "false")
        monkeypatch.delenv("TEACHER_SEED_2_FIRST_NAME", raising=False)
        _call()

        monkeypatch.setenv("TEACHER_SEED_1_LAST_NAME", "NewName")
        monkeypatch.setenv("TEACHER_SEED_1_ADMIN", "True")
        _call()

        t = Teacher.objects.get(email="julia@example.com")
        assert t.last_name == "NewName"
        assert t.admin is True
        assert t.user.is_superuser is True

    def test_missing_required_fields_skipped(self, monkeypatch):
        monkeypatch.setenv("TEACHER_SEED_1_FIRST_NAME", "Incomplete")
        # No LAST_NAME, no EMAIL
        monkeypatch.delenv("TEACHER_SEED_1_LAST_NAME", raising=False)
        monkeypatch.delenv("TEACHER_SEED_1_EMAIL", raising=False)
        monkeypatch.setenv("TEACHER_SEED_2_FIRST_NAME", "Complete")
        monkeypatch.setenv("TEACHER_SEED_2_LAST_NAME", "Good")
        monkeypatch.setenv("TEACHER_SEED_2_EMAIL", "complete@example.com")
        monkeypatch.setenv("TEACHER_SEED_2_ADMIN", "True")
        monkeypatch.setenv("TEACHER_SEED_2_PASSWORD", "pw")
        monkeypatch.delenv("TEACHER_SEED_3_FIRST_NAME", raising=False)

        output = _call()
        assert "Skipping" in output
        assert Teacher.objects.filter(email="complete@example.com").exists()
        assert not Teacher.objects.filter(first_name="Incomplete").exists()

    def test_stops_at_first_gap(self, monkeypatch):
        monkeypatch.setenv("TEACHER_SEED_1_FIRST_NAME", "First")
        monkeypatch.setenv("TEACHER_SEED_1_LAST_NAME", "One")
        monkeypatch.setenv("TEACHER_SEED_1_EMAIL", "first@example.com")
        monkeypatch.setenv("TEACHER_SEED_1_ADMIN", "True")
        monkeypatch.setenv("TEACHER_SEED_1_PASSWORD", "pw")
        monkeypatch.delenv("TEACHER_SEED_2_FIRST_NAME", raising=False)
        # Seed #3 should NOT be reached (gap at 2)
        monkeypatch.setenv("TEACHER_SEED_3_FIRST_NAME", "Third")
        monkeypatch.setenv("TEACHER_SEED_3_LAST_NAME", "Three")
        monkeypatch.setenv("TEACHER_SEED_3_EMAIL", "third@example.com")

        _call()
        assert Teacher.objects.filter(email="first@example.com").exists()
        assert not Teacher.objects.filter(email="third@example.com").exists()

    def test_env_bool_parsing_various_truthy_values(self, monkeypatch):
        for truthy in ("True", "1", "yes", "Y", "t"):
            Teacher.objects.filter(email=f"t-{truthy}@example.com").delete()
            monkeypatch.setenv("TEACHER_SEED_1_FIRST_NAME", f"T{truthy}")
            monkeypatch.setenv("TEACHER_SEED_1_LAST_NAME", "Test")
            monkeypatch.setenv("TEACHER_SEED_1_EMAIL", f"t-{truthy}@example.com")
            monkeypatch.setenv("TEACHER_SEED_1_ADMIN", truthy)
            monkeypatch.setenv("TEACHER_SEED_1_PASSWORD", "pw")
            monkeypatch.delenv("TEACHER_SEED_2_FIRST_NAME", raising=False)
            _call()
            t = Teacher.objects.get(email=f"t-{truthy}@example.com")
            assert t.admin is True, f"{truthy} should be parsed as True"

    def test_username_override_sets_the_login_handle(self, monkeypatch):
        monkeypatch.setenv("TEACHER_SEED_1_FIRST_NAME", "Claudia")
        monkeypatch.setenv("TEACHER_SEED_1_LAST_NAME", "Moreno")
        monkeypatch.setenv("TEACHER_SEED_1_EMAIL", "claudia@example.com")
        monkeypatch.setenv("TEACHER_SEED_1_USERNAME", "claudia")
        monkeypatch.setenv("TEACHER_SEED_1_ADMIN", "True")
        monkeypatch.setenv("TEACHER_SEED_1_PASSWORD", "pw")
        monkeypatch.delenv("TEACHER_SEED_2_FIRST_NAME", raising=False)

        output = _call()

        teacher = Teacher.objects.get(email="claudia@example.com")
        assert teacher.user.username == "claudia"
        # The Teacher's real email is unchanged — it is where mail goes.
        assert teacher.email == "claudia@example.com"
        assert teacher.user.email == "claudia@example.com"
        assert "login: claudia" in output

    def test_username_override_survives_a_re_run(self, monkeypatch):
        # `ensure_user` and the Teacher post_save signal both rewrite
        # `username` from the email — but only when the EMAIL changed. A second
        # boot of the container must not silently take the handle away.
        monkeypatch.setenv("TEACHER_SEED_1_FIRST_NAME", "Claudia")
        monkeypatch.setenv("TEACHER_SEED_1_LAST_NAME", "Moreno")
        monkeypatch.setenv("TEACHER_SEED_1_EMAIL", "claudia@example.com")
        monkeypatch.setenv("TEACHER_SEED_1_USERNAME", "claudia")
        monkeypatch.setenv("TEACHER_SEED_1_PASSWORD", "pw")
        monkeypatch.delenv("TEACHER_SEED_2_FIRST_NAME", raising=False)

        _call()
        _call()

        assert Teacher.objects.get(email="claudia@example.com").user.username == "claudia"
        assert User.objects.filter(email="claudia@example.com").count() == 1

    def test_without_username_the_handle_stays_the_email(self, monkeypatch):
        monkeypatch.setenv("TEACHER_SEED_1_FIRST_NAME", "Plain")
        monkeypatch.setenv("TEACHER_SEED_1_LAST_NAME", "Teacher")
        monkeypatch.setenv("TEACHER_SEED_1_EMAIL", "plain@example.com")
        monkeypatch.delenv("TEACHER_SEED_1_USERNAME", raising=False)
        monkeypatch.setenv("TEACHER_SEED_1_PASSWORD", "pw")
        monkeypatch.delenv("TEACHER_SEED_2_FIRST_NAME", raising=False)

        _call()
        assert Teacher.objects.get(email="plain@example.com").user.username == "plain@example.com"
