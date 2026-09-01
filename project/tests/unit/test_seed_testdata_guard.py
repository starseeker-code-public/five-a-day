"""`seed_testdata --reset` must refuse to run in production.

It unconditionally deletes every student, parent, enrollment, payment, group
and expense. The HTTP route into it is gated (`qa_access_required`), but the
command itself would run anywhere — one mis-targeted `gcloud run jobs execute`
away from wiping the academy. There is deliberately no override flag.
"""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

pytestmark = pytest.mark.django_db


class TestSeedResetGuard:
    def test_reset_is_refused_in_production(self):
        with override_settings(ENVIRONMENT="production"):
            with pytest.raises(CommandError, match="bloqueado en produccion"):
                call_command("seed_testdata", reset=True, stdout=StringIO())

    def test_plain_seed_still_reports_in_production(self, student):
        """Without --reset the command only refuses to seed over existing data
        — that pre-existing behaviour must survive the guard."""
        out = StringIO()
        with override_settings(ENVIRONMENT="production"):
            call_command("seed_testdata", stdout=out)
        assert "already has students" in out.getvalue()

    def test_reset_passes_the_guard_outside_production(self):
        """Outside production the guard must not fire. The command then fails
        further in (it requires the seeded admin teachers), and THAT error —
        not CommandError from the guard — is the proof the guard let it
        through, without paying for a full seed run in a unit test."""
        with override_settings(ENVIRONMENT="development"):
            with pytest.raises(RuntimeError, match="admin teachers"):
                call_command("seed_testdata", reset=True, small=True, stdout=StringIO())
