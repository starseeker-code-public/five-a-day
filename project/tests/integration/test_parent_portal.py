"""Integration tests for the parent portal (v1.9, email+password auth in v1.27)."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse

from core.views.parent_portal import PORTAL_TEMPORARY_PASSWORD_COOLDOWN
from students.models import PORTAL_AUTH_PASSWORD, PORTAL_AUTH_TEMPORARY, Parent

pytestmark = pytest.mark.django_db


def _login_as(client, parent):
    session = client.session
    session["parent_id"] = parent.id
    session.save()
    return client


class TestPasswordLogin:
    def test_login_page_loads(self, client):
        response = client.get(reverse("parent_portal_login"))
        assert response.status_code == 200

    def test_login_page_offers_a_password_field_not_a_magic_link(self, client):
        body = client.get(reverse("parent_portal_login")).content.decode()

        assert 'name="password"' in body
        assert reverse("parent_portal_forgot_password") in body

    def test_correct_credentials_start_a_session(self, client, parent):
        parent.set_portal_password("Portal-Fam-2026")

        response = client.post(
            reverse("parent_portal_login"),
            {"email": parent.email, "password": "Portal-Fam-2026"},
        )

        assert response.status_code == 302
        assert response.url == reverse("parent_portal_dashboard")
        assert client.session.get("parent_id") == parent.id

    def test_email_match_is_case_insensitive(self, client, parent):
        parent.set_portal_password("Portal-Fam-2026")

        response = client.post(
            reverse("parent_portal_login"),
            {"email": parent.email.upper(), "password": "Portal-Fam-2026"},
        )

        assert client.session.get("parent_id") == parent.id
        assert response.status_code == 302

    def test_wrong_password_is_refused(self, client, parent):
        parent.set_portal_password("Portal-Fam-2026")

        response = client.post(
            reverse("parent_portal_login"),
            {"email": parent.email, "password": "not-it"},
        )

        assert response.status_code == 200
        assert "parent_id" not in client.session

    def test_parent_without_a_password_cannot_log_in_with_a_blank_one(self, client, parent):
        """The empty-string hash must never be treated as "matches anything"."""
        assert parent.password == ""

        response = client.post(reverse("parent_portal_login"), {"email": parent.email, "password": ""})

        assert response.status_code == 200
        assert "parent_id" not in client.session

    def test_unknown_email_and_wrong_password_are_indistinguishable(self, client, parent):
        """Enumeration protection: the portal must not confirm which addresses
        belong to families of the academy.

        Compared with the submitted address normalised out of both bodies —
        the form legitimately echoes what was typed, and that difference is
        not a signal about whether the account exists.
        """
        parent.set_portal_password("Portal-Fam-2026")

        unknown = client.post(reverse("parent_portal_login"), {"email": "nobody@example.com", "password": "x"})
        wrong = client.post(reverse("parent_portal_login"), {"email": parent.email, "password": "x"})

        def rendered_messages(response):
            return [str(m) for m in response.context["messages"]]

        assert unknown.status_code == wrong.status_code
        # The user-visible outcome, not the bytes: every response carries a
        # fresh CSP nonce, so byte equality could never hold and would only
        # test the nonce.
        assert rendered_messages(unknown) == rendered_messages(wrong)
        assert rendered_messages(unknown) == ["❌ Email o contraseña incorrectos"]

    def test_an_already_logged_in_parent_skips_the_form(self, client, parent):
        _login_as(client, parent)

        response = client.get(reverse("parent_portal_login"))

        assert response.status_code == 302
        assert response.url == reverse("parent_portal_dashboard")

    def test_login_is_post_or_get_only(self, client):
        assert client.put(reverse("parent_portal_login")).status_code == 405


class TestAmbiguousEmailIsRefused:
    """`Parent.email` is NOT unique — only `dni` is — so two rows can carry the
    same address (a couple sharing a mailbox, or a duplicate record). Resolving
    that with `.first()` would sign one family in and show them ANOTHER
    family's payment history, while the second parent could never sign in."""

    @pytest.fixture
    def twin(self, parent):
        parent.set_portal_password("Portal-Fam-2026")
        other = Parent.objects.create(
            first_name="Otro",
            last_name="Tutor",
            dni="99999998Y",
            phone="600999888",
            email=parent.email,
        )
        other.set_portal_password("Portal-Fam-2026")
        return other

    def test_login_is_refused_rather_than_resolved_arbitrarily(self, client, parent, twin):
        response = client.post(
            reverse("parent_portal_login"),
            {"email": parent.email, "password": "Portal-Fam-2026"},
        )

        assert response.status_code == 200
        assert "parent_id" not in client.session

    def test_recovery_issues_nothing_for_an_ambiguous_address(self, client, parent, twin):
        response = client.post(reverse("parent_portal_forgot_password"), {"email": parent.email})

        assert response.status_code == 200
        parent.refresh_from_db()
        twin.refresh_from_db()
        assert not parent.has_temporary_password
        assert not twin.has_temporary_password, (
            "a temporary password here would let whichever row won take over the other family"
        )


class TestPortalPasswordIsNotAStaffLogin:
    """A parent credential must never open the admin app. The portal
    deliberately stores its hash on `Parent` instead of creating an auth.User,
    because `core.views.auth._authenticate_teacher` authenticates ANY
    auth.User — so a family holding one would hold a staff login."""

    def test_no_auth_user_is_created_for_a_parent(self, client, parent):
        from django.contrib.auth import get_user_model

        parent.set_portal_password("Portal-Fam-2026")
        client.post(reverse("parent_portal_login"), {"email": parent.email, "password": "Portal-Fam-2026"})

        assert not get_user_model().objects.filter(email__iexact=parent.email).exists()

    def test_portal_credentials_are_refused_by_the_staff_login(self, client, parent):
        parent.set_portal_password("Portal-Fam-2026")

        response = client.post(reverse("login"), {"username": parent.email, "password": "Portal-Fam-2026"})

        assert response.status_code == 200
        assert not client.session.get("is_authenticated")


class TestForgotPassword:
    """Recovery emails a TEMPORARY PASSWORD, not a link.

    An emailed link that expires strands any family who did not read their mail
    that day, and every unexpired one still sitting in an inbox is a standing
    key to the account. A temporary password is spent by being used, and because
    this endpoint is unauthenticated it deliberately does NOT overwrite a
    password the family may still be using.
    """

    def test_page_loads(self, client):
        assert client.get(reverse("parent_portal_forgot_password")).status_code == 200

    def test_known_email_issues_a_temporary_password(self, client, parent):
        assert not parent.has_temporary_password

        response = client.post(reverse("parent_portal_forgot_password"), {"email": parent.email})

        assert response.status_code == 200
        parent.refresh_from_db()
        assert parent.has_temporary_password

    def test_the_emailed_password_actually_works(self, client, parent):
        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as send:
            client.post(reverse("parent_portal_forgot_password"), {"email": parent.email})

        emailed = send.call_args[1]["context"]["temporary_password"]
        parent.refresh_from_db()
        assert parent.authenticate_portal(emailed) == PORTAL_AUTH_TEMPORARY

    def test_the_temporary_password_is_stored_hashed(self, client, parent):
        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as send:
            client.post(reverse("parent_portal_forgot_password"), {"email": parent.email})

        emailed = send.call_args[1]["context"]["temporary_password"]
        parent.refresh_from_db()
        assert parent.temporary_password != emailed
        # Hasher-agnostic: settings_test swaps in MD5 for speed.
        assert "$" in parent.temporary_password, "must be a Django password hash, not plaintext"

    def test_it_does_not_invalidate_a_password_the_family_is_using(self, client, parent):
        """This endpoint takes nothing but an email address. Overwriting the real
        credential here would let anyone who knows a family's address lock them
        out of their own payment history."""
        parent.set_portal_password("Portal-Fam-2026")

        client.post(reverse("parent_portal_forgot_password"), {"email": parent.email})

        parent.refresh_from_db()
        assert parent.authenticate_portal("Portal-Fam-2026") == PORTAL_AUTH_PASSWORD

    def test_unknown_email_shows_the_same_page_and_issues_nothing(self, client):
        response = client.post(reverse("parent_portal_forgot_password"), {"email": "nobody@example.com"})

        assert response.status_code == 200
        assert "Revisa tu email" in response.content.decode()

    def test_a_reissue_after_the_cooldown_invalidates_the_first_password(self, client, parent):
        """Three clicks must not leave three working credentials in the family's
        mailbox — each issue overwrites the last.

        The two requests are separated by ageing `temporary_password_issued_at`
        past `PORTAL_TEMPORARY_PASSWORD_COOLDOWN`. Back-to-back requests are now
        deliberately coalesced into ONE issuance: this endpoint is
        unauthenticated and takes nothing but an email address, so rotating the
        credential on every hit let anyone replay the form to deny a family
        their recovery path indefinitely (and the newest mail in their inbox was
        dead before they could type it). The overwrite invariant this test
        protects still holds — it just holds per cooldown window rather than per
        request. `test_a_second_request_inside_the_cooldown_does_not_reissue`
        covers the other half.
        """
        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as send:
            client.post(reverse("parent_portal_forgot_password"), {"email": parent.email})
            first = send.call_args[1]["context"]["temporary_password"]

            parent.refresh_from_db()
            Parent.objects.filter(pk=parent.pk).update(
                temporary_password_issued_at=(
                    parent.temporary_password_issued_at - PORTAL_TEMPORARY_PASSWORD_COOLDOWN - timedelta(minutes=1)
                )
            )

            client.post(reverse("parent_portal_forgot_password"), {"email": parent.email})
            second = send.call_args[1]["context"]["temporary_password"]

        parent.refresh_from_db()
        assert first != second
        assert parent.authenticate_portal(first) is None, "the superseded password must stop working"
        assert parent.authenticate_portal(second) == PORTAL_AUTH_TEMPORARY

    def test_a_second_request_inside_the_cooldown_does_not_reissue(self, client, parent):
        """Replaying the unauthenticated form must not destroy a live credential.

        The family may be holding the first mail; rotating on the second hit is
        what turned this endpoint into a denial of their own recovery path.
        """
        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as send:
            client.post(reverse("parent_portal_forgot_password"), {"email": parent.email})
            first = send.call_args[1]["context"]["temporary_password"]
            calls_after_first = send.call_count

            response = client.post(reverse("parent_portal_forgot_password"), {"email": parent.email})

        # Same reassuring page either way — the response must not reveal which
        # branch ran, or it becomes an oracle for who has a pending recovery.
        assert response.status_code == 200
        assert "Revisa tu email" in response.content.decode()
        assert send.call_count == calls_after_first, "the cooldown must suppress the second send"

        parent.refresh_from_db()
        assert parent.authenticate_portal(first) == PORTAL_AUTH_TEMPORARY, (
            "the credential the family already received must still work"
        )


class TestTemporaryPasswordLogin:
    """Logging in with a temporary password buys exactly one thing: the
    change-password page. The credential is sitting in plaintext in an inbox, so
    the rest of the portal stays shut until it has been replaced."""

    @pytest.fixture
    def temp_password(self, client, parent):
        with patch("comms.services.email_service.EmailService.send_email", return_value=True) as send:
            client.post(reverse("parent_portal_forgot_password"), {"email": parent.email})
        return send.call_args[1]["context"]["temporary_password"]

    def _log_in(self, client, parent, password):
        return client.post(reverse("parent_portal_login"), {"email": parent.email, "password": password})

    def test_it_redirects_to_the_change_password_page(self, client, parent, temp_password):
        response = self._log_in(client, parent, temp_password)

        assert response.status_code == 302
        assert response.url == reverse("parent_portal_change_password")

    def test_every_other_page_bounces_back_to_it(self, client, parent, temp_password):
        self._log_in(client, parent, temp_password)

        for name in ("parent_portal_dashboard", "parent_portal_payments"):
            response = client.get(reverse(name))
            assert response.status_code == 302, name
            assert response.url == reverse("parent_portal_change_password"), name

    def test_the_nav_is_hidden_while_the_change_is_pending(self, client, parent, temp_password):
        """Three links that all bounce straight back here would only look broken."""
        self._log_in(client, parent, temp_password)

        body = client.get(reverse("parent_portal_change_password")).content.decode()
        assert reverse("parent_portal_payments") not in body

    def test_setting_a_password_retires_the_temporary_one(self, client, parent, temp_password):
        self._log_in(client, parent, temp_password)

        response = client.post(
            reverse("parent_portal_change_password"),
            {"password": "Portal-Fam-2026", "password_confirm": "Portal-Fam-2026"},
        )

        assert response.status_code == 302
        assert response.url == reverse("parent_portal_dashboard")
        parent.refresh_from_db()
        assert parent.authenticate_portal(temp_password) is None
        assert parent.authenticate_portal("Portal-Fam-2026") == PORTAL_AUTH_PASSWORD

    def test_the_portal_opens_once_the_change_is_done(self, client, parent, temp_password):
        self._log_in(client, parent, temp_password)
        client.post(
            reverse("parent_portal_change_password"),
            {"password": "Portal-Fam-2026", "password_confirm": "Portal-Fam-2026"},
        )

        assert client.get(reverse("parent_portal_dashboard")).status_code == 200

    def test_the_forced_form_does_not_ask_for_the_current_password(self, client, parent, temp_password):
        """They typed it seconds ago at the login form; asking again proves
        nothing and costs a support call."""
        self._log_in(client, parent, temp_password)

        body = client.get(reverse("parent_portal_change_password")).content.decode()
        assert 'name="current_password"' not in body

    def test_a_temporary_password_survives_a_rejected_change(self, client, parent, temp_password):
        """A mistyped confirmation must not strand the family."""
        self._log_in(client, parent, temp_password)

        client.post(
            reverse("parent_portal_change_password"),
            {"password": "Portal-Fam-2026", "password_confirm": "Portal-Fam-2027"},
        )

        parent.refresh_from_db()
        assert parent.authenticate_portal(temp_password) == PORTAL_AUTH_TEMPORARY


class TestChangePassword:
    """The voluntary flow, reached from the portal nav."""

    @pytest.fixture
    def portal(self, client, parent):
        parent.set_portal_password("Portal-Fam-2026")
        client.post(reverse("parent_portal_login"), {"email": parent.email, "password": "Portal-Fam-2026"})
        return client

    def test_it_requires_a_session(self, client):
        response = client.get(reverse("parent_portal_change_password"))

        assert response.status_code == 302
        assert response.url == reverse("parent_portal_login")

    def test_the_current_password_is_required(self, portal, parent):
        """A session cookie is not proof that the person at the keyboard is the
        account holder — a shared family laptop is the ordinary case here, not
        the exotic one."""
        response = portal.post(
            reverse("parent_portal_change_password"),
            {"current_password": "wrong", "password": "Otra-Clave-2026", "password_confirm": "Otra-Clave-2026"},
        )

        assert response.status_code == 200
        parent.refresh_from_db()
        assert parent.authenticate_portal("Portal-Fam-2026") == PORTAL_AUTH_PASSWORD

    def test_a_correct_current_password_changes_it(self, portal, parent):
        response = portal.post(
            reverse("parent_portal_change_password"),
            {
                "current_password": "Portal-Fam-2026",
                "password": "Otra-Clave-2026",
                "password_confirm": "Otra-Clave-2026",
            },
        )

        assert response.status_code == 302
        parent.refresh_from_db()
        assert parent.authenticate_portal("Otra-Clave-2026") == PORTAL_AUTH_PASSWORD
        assert parent.authenticate_portal("Portal-Fam-2026") is None

    def test_a_temporary_password_cannot_authorise_a_voluntary_change(self, portal, parent):
        """Otherwise someone holding a recovery email could change the password
        without going through the forced flow — which is the path that proves
        they can read the family's mailbox."""
        raw = parent.issue_temporary_password()

        response = portal.post(
            reverse("parent_portal_change_password"),
            {"current_password": raw, "password": "Otra-Clave-2026", "password_confirm": "Otra-Clave-2026"},
        )

        assert response.status_code == 200
        parent.refresh_from_db()
        assert parent.authenticate_portal("Portal-Fam-2026") == PORTAL_AUTH_PASSWORD

    def test_the_new_password_must_differ_from_the_old(self, portal, parent):
        response = portal.post(
            reverse("parent_portal_change_password"),
            {
                "current_password": "Portal-Fam-2026",
                "password": "Portal-Fam-2026",
                "password_confirm": "Portal-Fam-2026",
            },
        )

        assert response.status_code == 200

    def test_mismatched_confirmation_is_refused(self, portal, parent):
        response = portal.post(
            reverse("parent_portal_change_password"),
            {
                "current_password": "Portal-Fam-2026",
                "password": "Otra-Clave-2026",
                "password_confirm": "Otra-Clave-2027",
            },
        )

        assert response.status_code == 200
        parent.refresh_from_db()
        assert parent.authenticate_portal("Portal-Fam-2026") == PORTAL_AUTH_PASSWORD

    @pytest.mark.parametrize("weak", ["abc", "1234567", "password", "12345678"])
    def test_a_weak_password_is_refused(self, portal, parent, weak):
        """Too short, a common password, and all-digits. These run against the
        REAL `PARENT_PASSWORD_VALIDATORS` — `settings_test` empties
        `AUTH_PASSWORD_VALIDATORS` for speed but leaves the portal's own set
        alone, so no override is needed and the test cannot pass vacuously."""
        response = portal.post(
            reverse("parent_portal_change_password"),
            {"current_password": "Portal-Fam-2026", "password": weak, "password_confirm": weak},
        )

        assert response.status_code == 200
        parent.refresh_from_db()
        assert parent.authenticate_portal("Portal-Fam-2026") == PORTAL_AUTH_PASSWORD

    def test_families_are_not_held_to_the_staff_password_bar(self, portal, parent):
        """`AUTH_PASSWORD_VALIDATORS` demands 12 characters because staff
        accounts are effectively superusers over a database of minors' personal
        data. A family gets a read-only view of their own children, so the portal
        has its own 8-character floor. An 8-character password the staff
        validators would reject must be accepted here — otherwise the two sets
        have been collapsed back into one."""
        response = portal.post(
            reverse("parent_portal_change_password"),
            {"current_password": "Portal-Fam-2026", "password": "casa-9713", "password_confirm": "casa-9713"},
        )

        assert response.status_code == 302
        parent.refresh_from_db()
        assert parent.authenticate_portal("casa-9713") == PORTAL_AUTH_PASSWORD

    def test_the_page_states_the_rules_it_actually_enforces(self, portal):
        """The help text is rendered from the validator set, so it cannot drift
        from what the POST will accept."""
        response = portal.get(reverse("parent_portal_change_password"))

        help_texts = " ".join(response.context["password_help"])
        assert "8" in help_texts
        assert "12" not in help_texts, "that is the staff rule, not the portal's"

    def test_the_stored_password_is_hashed(self, portal, parent):
        portal.post(
            reverse("parent_portal_change_password"),
            {
                "current_password": "Portal-Fam-2026",
                "password": "Otra-Clave-2026",
                "password_confirm": "Otra-Clave-2026",
            },
        )

        parent.refresh_from_db()
        assert parent.password != "Otra-Clave-2026"
        assert "$" in parent.password, "must be a Django password hash, not plaintext"


class TestInvitationIsSentExactlyOnce:
    """A family with three children is three trips through the enrolment flow
    and must still receive ONE invitation."""

    @pytest.fixture
    def staff(self, client):
        session = client.session
        session["is_authenticated"] = True
        session.save()
        return client

    PAYLOAD = {
        "first_name": "Invite",
        "last_name": "Test",
        "dni": "99999999Z",
        "phone": "600123123",
        "email": "invite.test@fiveaday.test",
        "iban": "",
    }

    def test_creating_a_parent_queues_the_invitation(self, staff):
        with patch("comms.tasks.send_parent_temporary_password_task.delay") as delay:
            staff.post(reverse("parent_create"), self.PAYLOAD)

        assert delay.call_count == 1
        assert delay.call_args[0][2] is False, "the invitation is not a reset"

        parent = Parent.objects.get(dni=self.PAYLOAD["dni"])
        assert parent.portal_invite_sent_at is not None
        assert not parent.has_portal_password, "the family chooses their own password"

    def test_a_second_child_for_the_same_family_sends_nothing(self, staff):
        with patch("comms.tasks.send_parent_temporary_password_task.delay"):
            staff.post(reverse("parent_create"), self.PAYLOAD)

        with patch("comms.tasks.send_parent_temporary_password_task.delay") as delay:
            staff.post(reverse("parent_create"), self.PAYLOAD)

        assert delay.call_count == 0

    def test_a_broken_mail_queue_does_not_break_the_enrolment(self, staff):
        """The invite is a side effect of creating the parent — losing it must
        not lose the record, because recovery is self-service anyway."""
        with patch(
            "comms.tasks.send_parent_temporary_password_task.delay",
            side_effect=RuntimeError("celery down"),
        ):
            response = staff.post(reverse("parent_create"), self.PAYLOAD)

        assert response.status_code == 302
        assert Parent.objects.filter(dni=self.PAYLOAD["dni"]).exists()


class TestPortalPages:
    def test_dashboard_requires_login(self, client):
        response = client.get(reverse("parent_portal_dashboard"))
        assert response.status_code == 302
        assert response.url == reverse("parent_portal_login")

    def test_dashboard_loads_for_logged_in_parent(self, client, parent):
        _login_as(client, parent)
        response = client.get(reverse("parent_portal_dashboard"))
        assert response.status_code == 200
        assert response.context["parent"] == parent

    def test_payments_history(self, client, parent, pending_payment):
        _login_as(client, parent)
        response = client.get(reverse("parent_portal_payments"), {"year": pending_payment.due_date.year})
        assert response.status_code == 200
        assert pending_payment in response.context["payments"]

    def test_receipt_only_for_own_payment(self, client, parent, second_parent, pending_payment):
        # Parent A owns pending_payment; second_parent must not be able to view it.
        _login_as(client, second_parent)
        response = client.get(reverse("parent_portal_receipt", args=[pending_payment.id]))
        assert response.status_code == 404

    def test_receipt_returns_pdf(self, client, parent, pending_payment):
        _login_as(client, parent)
        response = client.get(reverse("parent_portal_receipt", args=[pending_payment.id]))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"

    def test_tax_certificate(self, client, parent):
        _login_as(client, parent)
        response = client.get(reverse("parent_portal_tax_certificate"))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"

    def test_logout_clears_session(self, client, parent):
        _login_as(client, parent)
        response = client.post(reverse("parent_portal_logout"))
        assert response.status_code == 302
        assert "parent_id" not in client.session


class TestPortalQueryStringIsRangeChecked:
    """`?year=foo` was already handled; `?year=-5` and `?year=99999999999` were
    not. They parse as ints and then blow up where Django builds the bounds for
    the `due_date__year` lookup — a 500 from a hand-edited URL."""

    @pytest.fixture
    def portal(self, client, parent):
        session = client.session
        session["parent_id"] = parent.id
        session.save()
        return client

    @pytest.mark.parametrize("year", ["-5", "99999999999"])
    def test_the_payments_page_survives(self, portal, year):
        assert portal.get(reverse("parent_portal_payments"), {"year": year}).status_code == 200

    @pytest.mark.parametrize("year", ["-5", "99999999999"])
    def test_the_tax_certificate_survives(self, portal, year):
        response = portal.get(reverse("parent_portal_tax_certificate"), {"year": year})

        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
