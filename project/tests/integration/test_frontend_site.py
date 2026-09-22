"""The public React site — Django's half of serving frontend/dist.

The site used to deploy to Netlify and is now served from this origin, so
everything Netlify did for it is code here: the `/* -> /index.html` rewrite,
and the form handler. These tests cover the seams that move created, not React
itself.
"""

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from core.views.frontend import CONTACT_COOLDOWN_SECONDS, CONTACT_FIELDS, CSRF_META_NAME, SPA_ROUTES

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def contact_recipient(settings):
    """Pin the contact form's recipient instead of inheriting the environment.

    `CONTACT_FORM_RECIPIENT` falls back to `DEFAULT_FROM_EMAIL`, which is
    `EMAIL_HOST_USER`, which is EMPTY in CI — so without this the endpoint
    correctly answers 503 and every test that posts the form fails for a reason
    unrelated to the code under test. Same shape as the `_test_send_recipients`
    gotcha in CLAUDE.md: a test inheriting half its configuration from the
    developer's own `.env` passes locally and fails in CI, or the reverse.

    MODULE-LEVEL and autouse because it had been written out per class and the
    fourth class to need it did not get a copy: `TestTheCsrfTokenReachesTheReactBundle`
    posts a real form to prove the published token satisfies CsrfViewMiddleware,
    and in CI that POST answered 503 before CSRF was ever consulted — a green
    local run, a red CI one, and a failure message about the token that had
    nothing to do with the token. Nothing in this file tests the unset-recipient
    path, so there is no case this fixture takes away.
    """
    settings.CONTACT_FORM_RECIPIENT = "academia@example.com"


def _frontend_source(*parts: str) -> str:
    return (Path(settings.FRONTEND_DIR).joinpath(*parts)).read_text(encoding="utf-8")


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

    def test_each_route_gets_its_OWN_document_when_the_build_made_one(self, client, tmp_path, settings):
        """Per-route HTML is the whole point of `frontend/scripts/generate-seo.mjs`.

        This test used to assert the OPPOSITE — that every route returned a
        byte-identical shell — which was the correct contract under the Netlify
        rewrite and is the bug the SEO work exists to fix: seven pages sharing
        one `<title>` gave Google nothing to rank any of them by.

        A hand-built dist rather than the real one, because `frontend/dist` is
        gitignored and CI has no Node (see the `spa_shell` fixture): asserting
        against a real build would pass locally and be skipped-or-red in CI,
        which is how the v1.30.0 route tests went wrong.
        """
        dist = tmp_path / "dist"
        (dist / "faq").mkdir(parents=True)
        (dist / "index.html").write_text("<title>HOME</title>", encoding="utf-8")
        (dist / "faq" / "index.html").write_text("<title>FAQ</title>", encoding="utf-8")
        settings.FRONTEND_DIST_DIR = str(dist)

        assert b"FAQ" in client.get("/faq").content
        assert b"HOME" in client.get("/").content

    def test_a_route_with_no_generated_file_falls_back_to_the_shell(self, client, tmp_path, settings):
        """The fallback is load-bearing, not politeness.

        Three real situations have only `dist/index.html`: a developer who ran
        `vite build` without the generator, the `spa_shell` stub every CI run
        uses, and any future route added to `SPA_ROUTES` before `src/seo.js`.
        In all of them the page must still be served — just with the homepage's
        metadata, which costs ranking and breaks nothing.
        """
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<title>SHELL</title>", encoding="utf-8")
        settings.FRONTEND_DIST_DIR = str(dist)

        for route in SPA_ROUTES:
            response = client.get(f"/{route}")
            assert response.status_code == 200
            assert b"SHELL" in response.content

    def test_the_route_served_is_the_url_conf_kwarg_not_the_request_path(self, client, tmp_path, settings):
        """The file lookup may only ever name a value from `SPA_ROUTES`.

        `_index_path` joins its argument onto `FRONTEND_DIST_DIR`, so if that
        argument came from `request.path` it would be a directory-traversal
        question. It comes from a static extra kwarg in the URL conf instead,
        and this pins that: a traversal attempt matches no pattern at all.
        """
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<title>SHELL</title>", encoding="utf-8")
        settings.FRONTEND_DIST_DIR = str(dist)

        assert client.get("/../etc/passwd").status_code == 404
        assert client.get("/faq/../../etc/passwd").status_code == 404

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


class TestSeoMetadataCoversEveryPage:
    """`frontend/src/seo.js` is a third hand-kept list of the site's pages.

    `App.jsx` declares the routes, `SPA_ROUTES` serves them, and `seo.js` gives
    each one a title, description and canonical URL. All three have to agree,
    and the failure is silent in the usual direction: a page missing from
    `seo.js` still renders perfectly, it just gets the homepage's `<title>` and
    a canonical tag pointing at the homepage — which asks Google to treat it as
    a duplicate and drop it from the index.
    """

    def _seo_paths(self) -> set[str]:
        source = _frontend_source("src", "seo.js")
        # The `path:` key of each entry in `pagesSeo`.
        paths = set(re.findall(r'^\s*path:\s*"([^"]+)"', source, re.MULTILINE))
        assert paths, "parsed no paths out of seo.js — has its shape changed?"
        return paths

    def test_every_served_route_has_its_own_seo_entry(self):
        expected = {"/"} | {f"/{route}" for route in SPA_ROUTES}
        assert self._seo_paths() == expected, (
            f"seo.js and SPA_ROUTES disagree. Only in seo.js: {self._seo_paths() - expected}; "
            f"missing from seo.js: {expected - self._seo_paths()}"
        )

    def test_the_shell_keeps_the_markers_the_generator_writes_between(self):
        """`generate-seo.mjs` throws without these, so the build fails loudly —
        but only once somebody runs it. Failing here makes a stray edit to
        `frontend/index.html` cheap to find."""
        shell = _frontend_source("index.html")
        assert "<!-- SEO:START -->" in shell
        assert "<!-- SEO:END -->" in shell

    def test_titles_and_descriptions_stay_within_what_google_shows(self):
        """Google truncates around 60 characters of title and 155 of
        description. Over the limit is not an error, just words nobody reads —
        worth a nudge while the text is being written rather than after."""
        source = _frontend_source("src", "seo.js")
        titles = re.findall(r'^\s*title:\s*"([^"]+)"', source, re.MULTILINE)
        assert titles, "parsed no titles out of seo.js"
        too_long = [t for t in titles if len(t) > 65]
        assert not too_long, f"titles Google will truncate: {too_long}"

    def test_the_canonical_host_is_the_public_domain(self):
        """Every canonical, og:url and sitemap entry derives from SITE_URL. If
        it ever names the Cloud Run host or a QA address, Google is told the
        real pages live somewhere they do not."""
        source = _frontend_source("src", "seo.js")
        site_url = re.search(r'export const SITE_URL = "([^"]+)"', source)
        assert site_url, "SITE_URL not found in seo.js"
        assert site_url.group(1) == "https://fiveadayenglish.com"


class TestTheEmailIsValidatedBeforeAnythingIsSent:
    """The view's half of `core.email_policy` (which has its own unit tests).

    What matters here is the WIRING: that a refused address stops the send, that
    the two refusal reasons produce different messages, and that the domain
    refusal offers a way round — the family it turns away is usually real.
    """

    @staticmethod
    def _payload(email):
        return {
            "nombre": "Ana",
            "email": email,
            "telefono": "600123456",
            "mensaje": "Hola",
        }

    @pytest.mark.parametrize("email", ["ana@mailinator.com", "ana@no-existe-este-dominio.test"])
    def test_a_domain_off_the_allowlist_sends_nothing(self, client, email):
        response = client.post(reverse("submit_contact_form"), self._payload(email))
        assert response.status_code == 400
        assert response.json()["success"] is False
        assert mail.outbox == []

    def test_the_domain_refusal_names_a_channel_that_still_works(self):
        """An allowlist WILL occasionally refuse a real family — a work address,
        a small ISP. A dead end there is a lost enquiry, so the message has to
        carry a route: another provider, WhatsApp, or the academy's address."""
        client = Client()
        response = client.post(reverse("submit_contact_form"), self._payload("ana@una-empresa.test"))
        error = response.json()["error"]
        assert "WhatsApp" in error
        assert "hellofiveaday@gmail.com" in error

    @pytest.mark.parametrize("email", ["ana", "ana@@gmail.com", "ana garcia@gmail.com", "ana@gmail"])
    def test_a_malformed_address_sends_nothing(self, client, email):
        response = client.post(reverse("submit_contact_form"), self._payload(email))
        assert response.status_code == 400
        assert mail.outbox == []

    def test_a_malformed_address_is_not_told_to_change_provider(self):
        """Two different problems, two different answers. Telling somebody who
        mistyped their own address that their PROVIDER is unacceptable sends
        them off to create an account they did not need."""
        client = Client()
        error = client.post(reverse("submit_contact_form"), self._payload("ana@@gmail.com")).json()["error"]
        assert "WhatsApp" not in error

    def test_the_address_is_normalised_before_it_reaches_the_reply_to(self):
        client = Client()
        response = client.post(reverse("submit_contact_form"), self._payload("  Ana@GMAIL.com  "))
        assert response.status_code == 200
        assert mail.outbox[0].reply_to == ["Ana@gmail.com"]

    def test_a_refusal_logs_the_domain_and_never_the_address(self):
        """This endpoint is public, so what arrives is a stranger's personal
        data. The domain is the actionable half — it is what tells the academy
        the allowlist needs widening.

        Asserts on the logger rather than `caplog`: settings.LOGGING sets
        `propagate: False` on the `core` logger, so its records never reach the
        root handler caplog attaches to and `caplog.text` stays empty even
        though the message is emitted (same reason as
        `test_security_hardening.TestRateLimiterCacheOutage`).
        """
        client = Client()
        with patch("core.views.frontend.logger") as mock_logger:
            client.post(reverse("submit_contact_form"), self._payload("ana.garcia@mailinator.com"))

        logged = [str(arg) for call in mock_logger.info.call_args_list for arg in call.args]
        # The domain is passed as its OWN `%s` argument, so this is an exact
        # match on one of them rather than a substring of the flattened record —
        # which is both the stronger assertion and what stops CodeQL reading
        # `"mailinator.com" in <string>` as a half-done URL host check.
        assert "mailinator.com" in logged
        assert not any("ana.garcia" in arg for arg in logged)


class TestTheCooldownCannotDriftFromTheForm:
    """The server enforces the wait; the button counts the same number down.

    Two copies of one number, so they are machine-checked — the direction that
    fails quietly is the button re-enabling EARLY, which turns an honest second
    click into a 429 the visitor did nothing to deserve.
    """

    def test_the_react_form_counts_down_the_server_s_own_cooldown(self):
        source = _frontend_source("src", "data.js")
        match = re.search(r"cooldownSeconds:\s*(\d+)", source)
        assert match, "data.js no longer declares cooldownSeconds"
        assert int(match.group(1)) == CONTACT_COOLDOWN_SECONDS


class TestTheCooldownFollowsASentMessage:
    """These run with the limiter switched ON, which the suite otherwise
    disables — every test client is 127.0.0.1 and the cache is shared, so a
    cooldown left set would throttle an unrelated test. Without the flag these
    would all pass vacuously, proving only that the control is off.
    """

    @pytest.fixture(autouse=True)
    def _throttled(self, settings):
        settings.RATELIMIT_ENABLE = True
        cache.clear()
        yield
        cache.clear()

    def test_a_refused_submission_does_not_start_the_cooldown(self, client):
        """The commonest refusal here is a mistyped email.

        A cooldown claimed by a decorator would be spent before the view ran, so
        the family would be told to correct the address and then refused for a
        minute when they did — the site appearing to break at the exact moment
        they fixed their own mistake.
        """
        payload = {"nombre": "Ana", "telefono": "600123456", "mensaje": "Hola"}

        refused = client.post(reverse("submit_contact_form"), {**payload, "email": "ana@gmial.commm"})
        assert refused.status_code == 400

        accepted = client.post(reverse("submit_contact_form"), {**payload, "email": "ana@gmail.com"})
        assert accepted.status_code == 200, "correcting the address was refused by a cooldown"
        assert len(mail.outbox) == 1

    def test_a_sent_message_does_start_it(self, client):
        payload = {"nombre": "Ana", "email": "ana@gmail.com", "telefono": "600123456", "mensaje": "Hola"}

        assert client.post(reverse("submit_contact_form"), payload).status_code == 200
        second = client.post(reverse("submit_contact_form"), payload)
        assert second.status_code == 429
        assert len(mail.outbox) == 1, "the cooldown let a second message through"

    def test_the_cooldown_429_is_json_so_the_form_can_explain_it(self, client):
        """The rate limiter's own 429 is text/plain, which the React handler
        cannot parse — it would report the generic "no hemos podido enviar" and
        blame the send for what is a wait."""
        payload = {"nombre": "Ana", "email": "ana@gmail.com", "telefono": "600123456", "mensaje": "Hola"}
        client.post(reverse("submit_contact_form"), payload)

        second = client.post(reverse("submit_contact_form"), payload)
        assert second["Content-Type"].startswith("application/json")
        assert "WhatsApp" in second.json()["error"]


class TestTheCsrfTokenReachesTheReactBundle:
    """The public site is a BUILT file, so Django has to hand it the token.

    Every other page takes the CSRF token from markup Django rendered — the
    hidden `{% csrf_token %}` input in `base.html`, read by `base.js`. The
    React site has no such input, and `CSRF_COOKIE_HTTPONLY` is True whenever
    `DEBUG=False`, so the component's original `document.cookie` reader
    returned "" on the testing VM and in production: every contact submission
    was refused with a 403 whose HTML body the handler cannot parse, so it
    reported its generic "no hemos podido enviar el mensaje" and the fault read
    as a mail outage.

    It worked in development — where the cookie is NOT HttpOnly — which is the
    property that let it reach production. Both halves are pinned here because
    either alone is useless: Django must publish the token, and React must read
    the tag rather than the cookie.
    """

    def test_the_shell_carries_the_token_as_a_meta_tag(self, client, tmp_path, settings):
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html><head><title>x</title></head><body></body></html>", encoding="utf-8")
        settings.FRONTEND_DIST_DIR = str(dist)

        html = client.get("/").content.decode()
        match = re.search(rf'<meta name="{CSRF_META_NAME}" content="([^"]+)">', html)
        assert match, f"no {CSRF_META_NAME} meta tag in the served shell — every contact POST will 403"
        assert match.group(1), "the meta tag is present but empty"

    @pytest.mark.parametrize("route", SPA_ROUTES)
    def test_every_public_route_carries_it_too(self, client, route):
        """The form is in the shared layout, so it is reachable from any page."""
        assert f'name="{CSRF_META_NAME}"' in client.get(f"/{route}").content.decode()

    def test_the_token_is_accepted_by_a_real_post(self, client, tmp_path, settings):
        """End to end: the published token must satisfy CsrfViewMiddleware.

        `enforce_csrf_checks` because the test client disables CSRF by default,
        which is exactly what would let this regress unnoticed.
        """
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html><head></head><body></body></html>", encoding="utf-8")
        settings.FRONTEND_DIST_DIR = str(dist)

        checking = Client(enforce_csrf_checks=True)
        html = checking.get("/").content.decode()
        token = re.search(rf'<meta name="{CSRF_META_NAME}" content="([^"]+)">', html).group(1)

        response = checking.post(
            reverse("submit_contact_form"),
            data={"nombre": "Ana", "email": "ana@gmail.com", "telefono": "600", "mensaje": "Hola"},
            HTTP_X_CSRFTOKEN=token,
        )
        assert response.status_code == 200, "the published token was rejected — the two halves disagree"
        assert response.json()["success"] is True

    def test_the_react_form_reads_the_tag_and_not_the_cookie(self):
        """The JS half. A cookie reader here is the bug, restated."""
        source = _frontend_source("src", "components", "ContactSection.jsx")
        meta_at = source.find(CSRF_META_NAME)
        assert meta_at != -1, f"ContactSection.jsx does not read the {CSRF_META_NAME} meta tag"
        cookie_at = source.find("csrftoken=")
        assert cookie_at == -1 or cookie_at > meta_at, (
            "ContactSection.jsx reads document.cookie before the meta tag; the cookie is "
            "HttpOnly whenever DEBUG=False, so every submission 403s in testing and production"
        )


class TestContactForm:
    """Replaces Netlify Forms, which handled this while the site was deployed
    there and simply does not exist here.

    The failure being guarded against is specific: the React handler used to
    show "mensaje enviado" whatever came back, so a form delivering nowhere
    looked identical to one that worked — and nobody follows up on a message
    they believe was delivered.
    """

    @staticmethod
    def _payload(**overrides):
        data = {
            "nombre": "Ana",
            "apellidos": "García Ruiz",
            "email": "ana@gmail.com",
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
        for value in ("Ana", "García Ruiz", "ana@gmail.com", "600123456", "17:40"):
            assert value in body
        assert "quisiera información" in body

    def test_reply_to_is_the_family_so_a_plain_reply_reaches_them(self, client):
        """`From:` is the academy's own SMTP account and cannot be anything else.

        Without this header, hitting Reply on an enquiry answers
        hellofiveaday@gmail.com — the academy writing to itself — and the
        prospective family never hears back. The template's mailto button is a
        fallback for clients that ignore Reply-To, not a substitute: nobody
        scrolls to a button before pressing Reply.

        `submit_portfolio_contact` has had this since v1.30.4; the academy's own
        form, which runs every day, was still passing the address as template
        CONTEXT only.
        """
        client.post(reverse("submit_contact_form"), self._payload())
        assert mail.outbox[0].reply_to == ["ana@gmail.com"]
        assert mail.outbox[0].from_email == settings.DEFAULT_FROM_EMAIL
        assert mail.outbox[0].to == [settings.CONTACT_FORM_RECIPIENT]

    def test_the_visitor_is_never_a_recipient(self, client):
        """Reply-To is a HEADER, not an address the academy's mail is sent to.

        It also means `EMAIL_ALLOWED_RECIPIENTS` has nothing to filter here: the
        enquiry goes to the academy and nowhere else, so a visitor cannot make
        this endpoint deliver mail to an address of their choosing.
        """
        client.post(reverse("submit_contact_form"), self._payload())
        message = mail.outbox[0]
        assert "ana@gmail.com" not in (message.to + message.cc + message.bcc)

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
