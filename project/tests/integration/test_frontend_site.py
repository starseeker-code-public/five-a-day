"""The public React site — Django's half of serving frontend/dist.

The site used to deploy to Netlify and is now served from this origin, so
everything Netlify did for it is code here: the `/* -> /index.html` rewrite,
and the form handler. These tests cover the seams that move created, not React
itself.
"""

import json
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.core import mail
from django.test import Client
from django.urls import reverse

from core.views.frontend import CONTACT_FIELDS, SPA_ROUTES

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return Client()


def _frontend_source(*parts: str) -> str:
    return (Path(settings.FRONTEND_DIR).joinpath(*parts)).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _spa_shell(tmp_path, settings):
    """Guarantee there is an index.html to serve, without needing a real build.

    `frontend/dist` is a BUILD ARTEFACT: gitignored, produced by
    `make frontend-build` locally and by the Dockerfile's node stage in CI. The
    `Tests` job has no Node, so on a fresh clone and in CI the file is simply
    absent and every serving test 404s — which is what happened on the first
    push of v1.30.0: green locally, red in CI.

    What these tests are about is Django's serving behaviour — the routing, the
    caching header, the CSRF cookie — not the contents of the bundle, so a stub
    is the honest subject. It is written to a per-test `tmp_path` and pointed at
    with the `settings` fixture rather than into the real tree: the suite runs
    under `xdist -n auto`, and workers sharing one filesystem raced on creating
    and deleting a shared stub. A real build is used as-is when present.
    """
    if (Path(settings.FRONTEND_DIST_DIR) / "index.html").exists():
        yield
        return
    (tmp_path / "index.html").write_text("<!doctype html><title>stub</title><div id=root></div>", encoding="utf-8")
    settings.FRONTEND_DIST_DIR = str(tmp_path)
    yield


class TestSpaRoutesMatchReact:
    """`SPA_ROUTES` is a hand-kept mirror of App.jsx, so it is machine-checked.

    Drift is silent in the direction that matters: a route added in React but
    missing here 404s on a direct load or a refresh, while every click inside
    the SPA still works — so it survives all the testing anybody does by
    browsing the site.
    """

    def test_every_react_route_is_served_by_django(self):
        declared = set(re.findall(r'<Route\s+path="([^"]+)"', _frontend_source("src", "App.jsx")))
        assert declared, "parsed no routes out of App.jsx — has its markup changed?"
        assert declared == set(SPA_ROUTES), (
            f"App.jsx and SPA_ROUTES disagree. Only in App.jsx: {declared - set(SPA_ROUTES)}; "
            f"only in SPA_ROUTES: {set(SPA_ROUTES) - declared}"
        )

    def test_the_index_route_is_the_site_root(self):
        """React declares "/" as `<Route index>`, which Django serves as ""."""
        assert "<Route index" in _frontend_source("src", "App.jsx")
        assert reverse("public_home") == "/"


class TestServingTheShell:
    @pytest.mark.parametrize("route", SPA_ROUTES)
    def test_each_public_route_returns_the_spa_shell(self, client, route):
        response = client.get(f"/{route}")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")

    def test_every_route_returns_the_SAME_document(self, client):
        """React reads the path from the URL bar; the server sends one shell."""
        home = client.get("/").content
        for route in SPA_ROUTES:
            assert client.get(f"/{route}").content == home

    def test_routes_carry_no_trailing_slash(self, client):
        """They are registered exactly as React and the nav links spell them.
        A trailing-slash registration would cost an APPEND_SLASH redirect on
        every direct load and every refresh."""
        assert client.get("/faq").status_code == 200

    def test_the_shell_is_not_browser_cached(self, client):
        """Vite content-hashes the bundle, so a cached shell pins the PREVIOUS
        deploy's asset hashes — the staleness NoHtmlCacheMiddleware exists
        for."""
        assert "no-cache" in client.get("/")["Cache-Control"]

    def test_it_sets_a_csrf_cookie(self, client):
        """Without this the contact form cannot POST at all: the shell is a
        file read, not a rendered template, so nothing else would mint a token
        and CsrfViewMiddleware would refuse every submission."""
        assert "csrftoken" in client.get("/").cookies

    def test_an_unknown_path_is_a_404(self, client):
        """Enumerated routes, not a catch-all — React Router has no catch-all
        route either, so a catch-all here would answer every typo with a 200
        and a blank page."""
        assert client.get("/no-such-page").status_code == 404

    def test_the_app_and_health_are_untouched(self, client):
        assert client.get("/health/").status_code == 200
        assert client.get("/app/").status_code == 302


class TestContactForm:
    """Replaces Netlify Forms, which handled this while the site was deployed
    there and simply does not exist here.

    The failure being guarded against is specific: the React handler used to
    show "mensaje enviado" whatever came back, so a form delivering nowhere
    looked identical to one that worked — and nobody follows up on a message
    they believe was delivered.
    """

    @pytest.fixture(autouse=True)
    def _recipient(self, settings):
        """Pin the recipient instead of inheriting it from the environment.

        It defaults to DEFAULT_FROM_EMAIL, which is EMAIL_HOST_USER, which is
        empty in CI — so without this the endpoint correctly answers 503 and
        every test below fails for a reason unrelated to the code under test.
        Same shape as the `_test_send_recipients` gotcha in CLAUDE.md: a test
        that inherits half its configuration from the developer's own `.env`
        passes locally and fails in CI, or the reverse.
        """
        settings.CONTACT_FORM_RECIPIENT = "academia@example.com"

    @staticmethod
    def _payload(**overrides):
        data = {
            "nombre": "Ana",
            "apellidos": "García Ruiz",
            "email": "ana@example.com",
            "telefono": "600123456",
            "horario": "17:40",
            "edad": "7",
            "mensaje": "Hola, quisiera información sobre las clases.",
        }
        data.update(overrides)
        return data

    @staticmethod
    def _html_body() -> str:
        return mail.outbox[0].alternatives[0][0]  # type: ignore[union-attr,return-value]

    def test_a_valid_submission_emails_the_academy(self, client):
        response = client.post(reverse("submit_contact_form"), self._payload())
        assert response.status_code == 200
        assert json.loads(response.content)["success"] is True
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [settings.CONTACT_FORM_RECIPIENT]

    def test_it_defaults_to_the_academy_not_to_support(self):
        """SUPPORT_EMAIL is the developer's channel (QA tickets, error alerts);
        a prospective family asking about classes is the academy's business.

        Asserted against the WIRING in settings.py rather than the resolved
        value, which is environment-dependent — and empty in CI.
        """
        source = (Path(settings.BASE_DIR) / "project" / "settings.py").read_text(encoding="utf-8")
        assert 'CONTACT_FORM_RECIPIENT = os.getenv("CONTACT_FORM_RECIPIENT") or DEFAULT_FROM_EMAIL' in source

    def test_the_email_carries_every_submitted_field(self, client):
        client.post(reverse("submit_contact_form"), self._payload())
        body = self._html_body()
        for value in ("Ana", "García Ruiz", "ana@example.com", "600123456", "17:40"):
            assert value in body
        assert "quisiera información" in body

    def test_the_subject_names_the_sender(self, client):
        client.post(reverse("submit_contact_form"), self._payload())
        assert "Ana García Ruiz" in mail.outbox[0].subject

    def test_the_email_opts_out_of_client_dark_mode(self, client):
        """It extends base_email.html, which declares the message light-only.

        That is what makes it render correctly for a recipient on a dark
        client. An adaptive `prefers-color-scheme` variant was tried and STILL
        rendered dark and wrong in Outlook, which takes the scheme from the OS
        theme rather than its own toggle — see the email gotcha in CLAUDE.md.
        """
        client.post(reverse("submit_contact_form"), self._payload())
        body = self._html_body()
        assert 'name="color-scheme" content="light"' in body
        assert "prefers-color-scheme" not in body

    @pytest.mark.parametrize("field", ["nombre", "email", "telefono", "mensaje"])
    def test_a_missing_required_field_is_refused(self, client, field):
        response = client.post(reverse("submit_contact_form"), self._payload(**{field: ""}))
        assert response.status_code == 400
        assert json.loads(response.content)["success"] is False
        assert not mail.outbox

    def test_optional_fields_may_be_blank(self, client):
        response = client.post(reverse("submit_contact_form"), self._payload(apellidos="", horario="", edad=""))
        assert response.status_code == 200
        assert len(mail.outbox) == 1

    def test_a_blank_optional_field_is_not_rendered_as_an_empty_row(self, client):
        client.post(reverse("submit_contact_form"), self._payload(edad="", horario=""))
        body = self._html_body()
        assert "Edad del estudiante" not in body
        assert "Horario preferente" not in body

    def test_the_honeypot_is_accepted_and_discarded(self, client):
        """A cheerful 200 with nothing sent. Telling a spammer they were
        rejected only teaches them to leave the field alone next time."""
        response = client.post(reverse("submit_contact_form"), self._payload(**{"bot-field": "x"}))
        assert response.status_code == 200
        assert json.loads(response.content)["success"] is True
        assert not mail.outbox

    def test_unlisted_fields_are_dropped(self, client):
        """The email's LABELS come from CONTACT_FIELDS, never from the request
        — a submitter who chooses the labels decides what the reader believes
        the message is about. Same rule as SUPPORT_CATEGORIES."""
        client.post(
            reverse("submit_contact_form"),
            self._payload(**{"asunto": "URGENTE: factura impagada"}),
        )
        assert "factura impagada" not in self._html_body()

    def test_values_are_escaped_not_rendered(self, client):
        """The endpoint is public and unauthenticated; the reader is staff."""
        client.post(reverse("submit_contact_form"), self._payload(nombre="<script>alert(1)</script>"))
        body = self._html_body()
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;" in body

    def test_an_over_long_value_is_truncated_not_refused(self, client):
        """A family pasting a wall of text should still be heard from."""
        response = client.post(reverse("submit_contact_form"), self._payload(mensaje="x" * 50_000))
        assert response.status_code == 200
        assert len(self._html_body()) < 40_000

    def test_get_is_refused(self, client):
        assert client.get(reverse("submit_contact_form")).status_code == 405

    def test_the_field_list_matches_the_react_form(self):
        """`CONTACT_FIELDS` mirrors `contactForm.fields` in data.js. A field
        added on one side only is silently dropped by the other: the form would
        collect it and the email would never show it."""
        data_js = _frontend_source("src", "data.js")
        block = data_js.split("export const contactForm")[1].split("submitLabel")[0]
        declared = set(re.findall(r'\{\s*name:\s*"([^"]+)"', block))
        assert declared == {name for name, _, _ in CONTACT_FIELDS}


class TestTheNavLinksIntoTheApp:
    """The public site's way in to the Django app, and the parent portal link
    that must NOT be there yet."""

    def test_the_app_button_points_at_the_app_prefix(self):
        data_js = _frontend_source("src", "data.js")
        assert f'path: "{settings.APP_PATH_PREFIX}/"' in data_js

    def test_the_parent_portal_link_is_hidden_while_the_portal_is_off(self):
        """The two sides must agree about whether families can see the portal.

        `PARENT_PORTAL_ENABLED` is False, so every /parent/ URL is a 404 —
        including the login page, so that a family following an old link finds
        nothing rather than a form that cannot help them. A visible button into
        that is a dead end carrying the academy's name.

        The button is kept as LIVE code behind `appAccess.parents.enabled`
        rather than commented out, so it stays compiled, linted and rendered by
        the frontend tests while it waits. This asserts the flag is off and
        that the markup is genuinely gated on it — a flag nothing reads would
        pass the first half and still show the button.
        """
        data_js = _frontend_source("src", "data.js")
        navbar = _frontend_source("src", "components", "Navbar.jsx")

        parents_entry = data_js.split("parents:", 1)[1].split("},", 1)[0]
        assert "enabled: false" in parents_entry, "the parent portal button is switched ON in data.js"
        assert "appAccess.parents.enabled &&" in navbar, (
            "Navbar.jsx renders the parent portal link without checking the flag"
        )

    # NOT TESTED HERE: that the button's flag agrees with
    # settings.PARENT_PORTAL_ENABLED. It cannot be, and the reason is worth
    # writing down so it is not attempted again — it looks like the obvious
    # test to add.
    #
    # settings_test.py turns PARENT_PORTAL_ENABLED back ON for the whole suite
    # (the feature is switched off, not withdrawn, and the ~90 tests that prove
    # the portal works have to run), so `settings.PARENT_PORTAL_ENABLED` is
    # True in here regardless of what production does. Comparing against it
    # would assert that the button is VISIBLE — the opposite of what is wanted.
    #
    # Nor is the repo the right place for that comparison anyway: the setting
    # is env-driven and could legitimately differ per environment, while
    # data.js is one committed file. Turning the portal on is a two-part change
    # (the env var AND the flag), and the note beside `parents` in data.js is
    # what records the second half.

    def test_the_app_link_is_a_plain_anchor_not_a_router_link(self):
        """`/app/` is served by Django. A <Link>/<NavLink> would navigate
        client-side, match no <Route>, and render a blank page while the URL
        bar showed the right address."""
        navbar = _frontend_source("src", "components", "Navbar.jsx")
        for line in navbar.splitlines():
            if "appAccess.staff.path" in line:
                assert "href=" in line
