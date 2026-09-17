"""The parent-portal kill switch — `settings.PARENT_PORTAL_ENABLED`.

The portal is DISABLED in every real environment. It is switched off, not
withdrawn: `settings_test.PARENT_PORTAL_ENABLED = True` so the rest of the
suite keeps proving the feature works, and THIS module is the one place that
overrides it back to False to prove the switch actually bites.

Three things have to hold, and each was a separate way of getting it wrong:

* every `/parent/` URL 404s, including the public login and recovery pages —
  a family holding an old link must find nothing, not a form that cannot help;
* no access email is queued, by any of the three senders;
* `Parent.portal_invite_sent_at` is NOT stamped, so turning the portal back on
  later still invites every family exactly once. Stamping while the mail is
  suppressed would burn the once-only guard on an email nobody received.
"""

import pytest
from django.core import mail
from django.urls import reverse

from core.services.portal_access_service import (
    send_portal_invitation_once,
    send_portal_temporary_password,
)
from students.models import Parent

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def portal_off(settings):
    """Every test in this module runs with the portal switched OFF.

    `settings_test` turns it ON for the whole suite (see the note there), so the
    override has to be local and autouse — a module-level `override_settings` in
    `pytestmark` is not a pytest Mark and fails at collection.
    """
    settings.PARENT_PORTAL_ENABLED = False


PORTAL_URL_NAMES = [
    "parent_portal_login",
    "parent_portal_forgot_password",
    "parent_portal_change_password",
    "parent_portal_dashboard",
    "parent_portal_payments",
    "parent_portal_tax_certificate",
]


class TestEveryPortalUrlIs404:
    @pytest.mark.parametrize("url_name", PORTAL_URL_NAMES)
    def test_a_portal_page_is_not_found(self, client, url_name):
        assert client.get(reverse(url_name)).status_code == 404

    def test_even_a_logged_in_family_gets_nothing(self, client, parent):
        # The gate runs before the portal session is read, so an already-open
        # session is not a way past it either.
        session = client.session
        session["parent_id"] = parent.id
        session.save()
        assert client.get(reverse("parent_portal_dashboard")).status_code == 404

    def test_the_staff_app_is_untouched(self, authenticated_client):
        assert authenticated_client.get(reverse("home")).status_code == 200


class TestNoAccessEmailIsSent:
    def test_creating_a_parent_sends_nothing_and_leaves_the_guard_unstamped(self, authenticated_client):
        mail.outbox.clear()
        response = authenticated_client.post(
            reverse("parent_create"),
            {
                "first_name": "Ana",
                "last_name": "Ruiz",
                "dni": "11223344C",
                "phone": "600111222",
                "email": "ana.ruiz@example.com",
                "iban": "ES1234567890123456789012",
            },
        )
        assert response.status_code in (200, 302)
        created = Parent.objects.get(dni="11223344C")
        assert created.portal_invite_sent_at is None, (
            "the once-only invitation guard must stay unstamped while the portal is off, "
            "or re-enabling it would leave this family permanently uninvited"
        )
        assert not any("portal" in message.subject.lower() for message in mail.outbox)

    def test_the_sender_refuses_and_never_raises(self, rf, parent):
        request = rf.get("/")
        assert send_portal_temporary_password(request, parent, reset=True) is False
        assert send_portal_invitation_once(request, parent) is False
        parent.refresh_from_db()
        assert parent.portal_invite_sent_at is None
        assert parent.temporary_password == ""
