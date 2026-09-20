"""The portfolio contact relay — `POST /api/portfolio/contact/`.

The endpoint is a second tenant of this deployment (see core/views/portfolio.py)
and it is PUBLIC-facing infrastructure guarded by one shared secret, so these
tests are weighted towards the ways that guard can quietly stop guarding:
an unset token read as "no authentication needed", a caller choosing its own
recipient, a caller choosing its own field labels, and the relay answering a
session redirect instead of JSON because it drifted under the app prefix.
"""

import json

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from core.views.portfolio import MAX_FIELD_LENGTH, MAX_MESSAGE_LENGTH, PORTFOLIO_FIELDS

pytestmark = pytest.mark.django_db

TOKEN = "test-portfolio-token"
RECIPIENT = "owner@example.com"


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def _relay_configured(settings):
    """Pin both settings instead of inheriting them from the environment.

    They default to empty, so without this every test below gets the honest 503
    ("not configured") and fails for a reason unrelated to the code under test —
    the same trap `_recipient` documents in test_frontend_site.py.
    """
    settings.PORTFOLIO_CONTACT_TOKEN = TOKEN
    settings.PORTFOLIO_CONTACT_RECIPIENT = RECIPIENT


def _payload(**overrides):
    data = {
        "name": "Dana Okafor",
        "email": "dana@example.com",
        "company": "Northwind",
        "subject": "Backend role",
        "message": "Hi — we are hiring a senior Python engineer.\nAre you open to a chat?",
    }
    data.update(overrides)
    return data


def _post(client, payload=None, token=TOKEN, **extra):
    headers = {} if token is None else {"HTTP_AUTHORIZATION": f"Bearer {token}"}
    headers.update(extra)
    return client.post(
        reverse("submit_portfolio_contact"),
        data=json.dumps(_payload() if payload is None else payload),
        content_type="application/json",
        **headers,
    )


def _html_body() -> str:
    return mail.outbox[0].alternatives[0][0]  # type: ignore[union-attr,return-value]


class TestDelivery:
    def test_a_valid_request_sends_one_email_to_the_configured_recipient(self, client):
        response = _post(client)
        assert response.status_code == 200
        assert json.loads(response.content)["success"] is True
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [RECIPIENT]

    def test_the_recipient_cannot_be_chosen_by_the_caller(self, client):
        """The whole blast radius of a leaked token rests on this.

        If a `to` in the body could redirect delivery, the endpoint would stop
        being "unsolicited mail to my own inbox" and become an open relay
        sending from this domain, with its sender reputation attached.
        """
        _post(client, _payload(**{"to": "victim@example.com", "recipients": "victim@example.com"}))
        assert mail.outbox[0].to == [RECIPIENT]

    def test_reply_to_is_the_sender_so_plain_reply_reaches_them(self, client):
        """`From:` is the academy's SMTP account and cannot be anything else, so
        without this header Reply answers the wrong mailbox entirely."""
        _post(client)
        assert mail.outbox[0].reply_to == ["dana@example.com"]

    def test_the_subject_carries_the_prefix_the_subject_line_and_the_name(self, client):
        _post(client)
        subject = mail.outbox[0].subject
        assert subject.startswith("[Portfolio] ")
        assert "Backend role" in subject
        assert "Dana Okafor" in subject

    def test_a_missing_subject_falls_back_rather_than_sending_a_bare_prefix(self, client):
        _post(client, _payload(subject=""))
        assert mail.outbox[0].subject == "[Portfolio] New message from the portfolio — Dana Okafor"

    def test_the_email_carries_every_submitted_field(self, client):
        _post(client)
        body = _html_body()
        for value in ("Dana Okafor", "dana@example.com", "Northwind", "Backend role"):
            assert value in body
        assert "senior Python engineer" in body

    def test_a_blank_optional_field_is_not_rendered_as_an_empty_row(self, client):
        _post(client, _payload(company="", subject=""))
        body = _html_body()
        assert "Company" not in body
        assert ">Subject<" not in body


class TestAuthentication:
    def test_no_authorization_header_is_refused(self, client):
        response = _post(client, token=None)
        assert response.status_code == 401
        assert not mail.outbox

    def test_a_wrong_token_is_refused(self, client):
        assert _post(client, token="not-the-token").status_code == 401
        assert not mail.outbox

    def test_a_bare_token_without_the_bearer_scheme_is_refused(self, client):
        response = client.post(
            reverse("submit_portfolio_contact"),
            data=json.dumps(_payload()),
            content_type="application/json",
            HTTP_AUTHORIZATION=TOKEN,
        )
        assert response.status_code == 401
        assert not mail.outbox

    def test_the_scheme_is_matched_case_insensitively(self, client):
        """RFC 7235 makes the scheme case-insensitive and HTTP clients differ on
        it; refusing `bearer` would be a defect that only shows up in prod."""
        response = client.post(
            reverse("submit_portfolio_contact"),
            data=json.dumps(_payload()),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"bearer {TOKEN}",
        )
        assert response.status_code == 200

    def test_every_rejection_looks_identical(self, client):
        """Distinguishing "no header" from "wrong token" hands an attacker a
        free oracle for the header shape before they start guessing."""
        bodies = {
            _post(client, token=None).content,
            _post(client, token="").content,
            _post(client, token="wrong").content,
        }
        assert len(bodies) == 1

    def test_an_unset_token_refuses_instead_of_opening_the_relay(self, client, settings):
        """The failure this guards is the expensive one: a deploy that forgets
        the secret must not fall back to accepting everyone. 503 says
        "not offered here", which is true, and no mail leaves either way."""
        settings.PORTFOLIO_CONTACT_TOKEN = ""
        assert _post(client, token=None).status_code == 503
        assert _post(client, token="anything").status_code == 503
        assert not mail.outbox

    def test_an_unset_recipient_refuses_rather_than_defaulting(self, client, settings):
        """Unlike CONTACT_FORM_RECIPIENT there is deliberately no fallback: the
        one this would inherit is the ACADEMY's inbox, and a stranger's message
        about contract work must not land on the school's staff."""
        settings.PORTFOLIO_CONTACT_RECIPIENT = ""
        assert _post(client).status_code == 503
        assert not mail.outbox


class TestValidation:
    @pytest.mark.parametrize("field", ["name", "email", "message"])
    def test_a_missing_required_field_is_refused(self, client, field):
        response = _post(client, _payload(**{field: ""}))
        assert response.status_code == 400
        assert json.loads(response.content)["success"] is False
        assert not mail.outbox

    def test_optional_fields_may_be_absent_entirely(self, client):
        payload = _payload()
        del payload["company"]
        del payload["subject"]
        assert _post(client, payload).status_code == 200
        assert len(mail.outbox) == 1

    def test_a_body_that_is_not_json_is_a_400_not_a_500(self, client):
        response = client.post(
            reverse("submit_portfolio_contact"),
            data="this is not json",
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
        )
        assert response.status_code == 400
        assert not mail.outbox

    @pytest.mark.parametrize("body", ["[]", "2", '"text"', "null"])
    def test_valid_json_that_is_not_an_object_is_a_400_not_a_500(self, client, body):
        """`json.loads("[]")` parses happily and then dies on `.get`. A client
        bug should not read as a server fault in the logs."""
        response = client.post(
            reverse("submit_portfolio_contact"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
        )
        assert response.status_code == 400
        assert not mail.outbox

    def test_a_null_value_does_not_become_the_string_none(self, client):
        response = _post(client, _payload(company=None))
        assert response.status_code == 200
        assert "None" not in _html_body()

    def test_an_over_long_message_is_truncated_not_refused(self, client):
        """Someone pasting a whole job description should still be heard from."""
        response = _post(client, _payload(message="x" * 50_000))
        assert response.status_code == 200
        body = _html_body()
        assert "x" * MAX_MESSAGE_LENGTH in body
        assert "x" * (MAX_MESSAGE_LENGTH + 1) not in body

    def test_the_message_is_rendered_once_not_twice(self, client):
        """It has its own quoted block, so it is kept OUT of the details table.

        Rendering it in both places doubles the single field that carries the
        5 000-character cap — an 18 KB email with the same wall of text in it
        twice, which is how this was found.
        """
        _post(client, _payload(message="x" * MAX_MESSAGE_LENGTH))
        assert _html_body().count("x" * MAX_MESSAGE_LENGTH) == 1

    def test_an_over_long_short_field_is_truncated(self, client):
        _post(client, _payload(name="n" * 5_000))
        assert "n" * (MAX_FIELD_LENGTH + 1) not in _html_body()

    def test_unlisted_fields_are_dropped(self, client):
        """The LABELS come from PORTFOLIO_FIELDS, never from the request — a
        caller who picks the labels decides what the reader believes the message
        is about. Same rule as CONTACT_FIELDS and SUPPORT_CATEGORIES."""
        _post(client, _payload(**{"invoice": "URGENT: unpaid invoice"}))
        assert "unpaid invoice" not in _html_body()

    def test_values_are_escaped_not_rendered(self, client):
        """Anyone on the internet can reach this through the form; the reader is
        the owner's own mail client."""
        _post(client, _payload(name="<script>alert(1)</script>"))
        body = _html_body()
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;" in body


class TestWiring:
    def test_get_is_refused(self, client):
        assert client.get(reverse("submit_portfolio_contact")).status_code == 405

    def test_it_answers_json_rather_than_a_login_redirect(self, client):
        """It sits at the ORIGIN ROOT, outside `settings.APP_PATH_PREFIX`, so
        SimpleAuthMiddleware passes it through. Moved under the app prefix it
        would answer this POST with a 302 to the staff login — a failure the
        caller can only see as "the form stopped working"."""
        response = _post(client, token=None)
        assert response.status_code == 401
        assert response["Content-Type"].startswith("application/json")

    def test_it_is_exempt_from_csrf(self, client):
        """The caller is a server holding a bearer token, with no cookie and no
        session — the ambient authority CSRF defends against does not exist
        here. Asserted through a CSRF-enforcing client so the exemption is
        proven rather than assumed."""
        enforcing = Client(enforce_csrf_checks=True)
        response = enforcing.post(
            reverse("submit_portfolio_contact"),
            data=json.dumps(_payload()),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
        )
        assert response.status_code == 200

    def test_the_field_list_is_declared_not_inferred(self):
        """Mirrors the portfolio's own form. A field added on one side only is
        silently dropped by the other: collected by the form, never shown in
        the email."""
        keys = [key for key, _, _ in PORTFOLIO_FIELDS]
        assert keys == ["name", "email", "company", "subject", "message"]
        assert [key for key, _, required in PORTFOLIO_FIELDS if required] == ["name", "email", "message"]

    def test_the_email_opts_out_of_client_dark_mode(self, client):
        """Same rule as every other template here: declared light-only, with no
        prefers-color-scheme variant, because Outlook takes the scheme from the
        OS theme and renders an adaptive email dark and wrong."""
        _post(client)
        body = _html_body()
        assert 'name="color-scheme" content="light"' in body
        assert "prefers-color-scheme" not in body

    def test_no_template_comment_survives_into_the_email(self, client):
        """Django's `{# … #}` comment is SINGLE-LINE only.

        Spread one across lines and it is not stripped — it is rendered, and
        the file's own reasoning is mailed to a stranger. This template was
        written that way first and shipped a 19 KB body whose opening line was
        a note about the `lang` attribute. Nothing announces it: the email
        still arrives and still reads correctly below the leak.
        """
        _post(client)
        body = _html_body()
        assert "{#" not in body
        assert "{%" not in body
        assert "IT DELIBERATELY DOES NOT EXTEND" not in body
        # Overhead is scaffold only — the cap is the message, not the file.
        assert len(body) < MAX_MESSAGE_LENGTH + 10_000

    def test_the_email_carries_none_of_the_academy_branding(self, client):
        """It deliberately does not extend base_email.html. That stationery
        carries the school's address, phone, socials and an AVISO LEGAL naming
        its data controller — every word of which would be a false claim about
        who processed this stranger's data."""
        _post(client)
        body = _html_body()
        for academy_marker in ("Five a Day", "AVISO LEGAL", "Hermanos Jiménez", "fiveadayenglish"):
            assert academy_marker not in body
