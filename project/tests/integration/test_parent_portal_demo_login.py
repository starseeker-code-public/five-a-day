"""
The parent portal's DEMO PARENT, seeded from DEMO_PARENT_<N>_* env vars.

Until v1.27 there was a second login mode here: the view compared a plaintext
password out of the environment, so the flow QA exercised was not the flow
production ran. The demo parent now holds a real hashed password on their
`Parent` row and signs in through the ordinary /parent/login/ form — the same
code path a real family uses. That is the property most of these tests pin.

The negative ones still matter most: the command must be impossible to run in
production, where its output would be a fake family in the academy's real roll
holding a password that also sits in the env set.
"""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.urls import reverse

from students.models import Parent

pytestmark = pytest.mark.django_db

DEMO_EMAIL = "demo.family@fiveaday.test"
DEMO_PASSWORD = "demo-pw-123"


@pytest.fixture(autouse=True)
def _clear_demo_env(monkeypatch):
    for index in range(1, 3):
        for suffix in ("USERNAME", "PASSWORD", "EMAIL", "FIRST_NAME", "LAST_NAME", "DNI", "PHONE", "IBAN", "CHILDREN"):
            monkeypatch.delenv(f"DEMO_PARENT_{index}_{suffix}", raising=False)


@pytest.fixture
def demo_env(monkeypatch):
    monkeypatch.setenv("DEMO_PARENT_1_USERNAME", "demofamily")
    monkeypatch.setenv("DEMO_PARENT_1_PASSWORD", DEMO_PASSWORD)
    monkeypatch.setenv("DEMO_PARENT_1_EMAIL", DEMO_EMAIL)
    monkeypatch.setenv("DEMO_PARENT_1_DNI", "12345678D")


class TestSeeding:
    def test_no_env_block_seeds_nothing(self, client):
        call_command("seed_demo_parents", verbosity=0)

        assert not Parent.objects.filter(email__iexact=DEMO_EMAIL).exists()

    def test_the_seeded_parent_gets_a_usable_password(self, demo_env):
        call_command("seed_demo_parents", verbosity=0)

        parent = Parent.objects.get(email__iexact=DEMO_EMAIL)
        assert parent.authenticate_portal(DEMO_PASSWORD) == "password"

    def test_the_password_is_stored_hashed_not_in_plaintext(self, demo_env):
        call_command("seed_demo_parents", verbosity=0)

        parent = Parent.objects.get(email__iexact=DEMO_EMAIL)
        assert parent.password != DEMO_PASSWORD
        # Hasher-agnostic: settings_test swaps in MD5 for speed.
        assert "$" in parent.password, "must be a Django password hash, not plaintext"

    def test_the_demo_parent_is_never_sent_an_invitation(self, demo_env):
        """It would contradict the password the command just set — and in
        development it would go nowhere anyway."""
        call_command("seed_demo_parents", verbosity=0)

        parent = Parent.objects.get(email__iexact=DEMO_EMAIL)
        assert parent.portal_invite_sent_at is not None

    def test_re_running_is_idempotent_and_restores_the_password(self, demo_env):
        call_command("seed_demo_parents", verbosity=0)
        parent = Parent.objects.get(email__iexact=DEMO_EMAIL)
        parent.set_portal_password("someone-changed-it-in-the-ui")

        call_command("seed_demo_parents", verbosity=0)

        assert Parent.objects.filter(email__iexact=DEMO_EMAIL).count() == 1
        parent.refresh_from_db()
        assert parent.authenticate_portal(DEMO_PASSWORD) == "password"

    @override_settings(ENVIRONMENT="production")
    def test_refused_in_production(self, demo_env):
        with pytest.raises(CommandError):
            call_command("seed_demo_parents", verbosity=0)

        assert not Parent.objects.filter(email__iexact=DEMO_EMAIL).exists()


class TestTheDemoParentUsesTheRealLoginPath:
    """No separate demo mode: whatever QA signs off on here is what a family
    in production executes."""

    def test_the_seeded_credential_logs_in_through_the_ordinary_form(self, client, demo_env):
        call_command("seed_demo_parents", verbosity=0)
        parent = Parent.objects.get(email__iexact=DEMO_EMAIL)

        response = client.post(
            reverse("parent_portal_login"),
            {"email": DEMO_EMAIL, "password": DEMO_PASSWORD},
        )

        assert response.status_code == 302
        assert client.session.get("parent_id") == parent.id

    def test_the_login_page_has_no_separate_demo_form(self, client, demo_env):
        call_command("seed_demo_parents", verbosity=0)

        body = client.get(reverse("parent_portal_login")).content

        assert b"demo_username" not in body
        assert b"demo_password" not in body

    def test_a_wrong_password_is_refused(self, client, demo_env):
        call_command("seed_demo_parents", verbosity=0)

        response = client.post(
            reverse("parent_portal_login"),
            {"email": DEMO_EMAIL, "password": "wrong"},
        )

        assert response.status_code == 200
        assert "parent_id" not in client.session

    def test_a_non_ascii_password_is_refused_not_a_500(self, client, demo_env):
        call_command("seed_demo_parents", verbosity=0)

        response = client.post(
            reverse("parent_portal_login"),
            {"email": DEMO_EMAIL, "password": "contraseña-ñ"},
        )

        assert response.status_code == 200
        assert "parent_id" not in client.session

    def test_the_session_is_flushed_so_admin_state_cannot_survive(self, client, demo_env):
        call_command("seed_demo_parents", verbosity=0)
        session = client.session
        session["is_authenticated"] = True
        session.save()

        client.post(reverse("parent_portal_login"), {"email": DEMO_EMAIL, "password": DEMO_PASSWORD})

        assert "is_authenticated" not in client.session
        assert client.session.get("parent_id")
