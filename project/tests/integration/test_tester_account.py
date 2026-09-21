"""The public tester account — reach, refusals, containment and nightly repair.

The tester is a SHARED credential published on an external portfolio site, so
"what can a stranger do with it" is the whole question this file exists to
answer. It is a THIRD role, deliberately between the two that existed:

    plain teacher  ⊂  tester  ⊂  admin

`Teacher.admin` is False, so it is not an administrator in the database, cannot
reach `/admin/`, and is excluded from the QA tools. What widens it is one
allowlist, `TESTER_ALLOWED_URL_NAMES`, read by BOTH the middleware and
`admin_required` — and the tests below drive that frozenset directly rather than
restating it, so the set and its enforcement cannot drift apart.

Four properties matter, and each fails silently without a test:

  * the role is strictly between the other two (the ⊂ above, asserted);
  * `/admin/` and the QA tools are refused;
  * two endpoints an ORDINARY teacher may use are refused to the tester —
    the one direction a reader will not expect;
  * and every change it makes is undone by `reset_tester_environment`.
"""

from datetime import timedelta

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse
from django.utils import timezone

from billing import constants
from billing.models import SiteConfiguration
from comms.services.email_service import email_service, filter_allowed_recipients
from core.decorators import may_use_qa_tools
from core.middleware import (
    NON_ADMIN_ALLOWED_URL_NAMES,
    TESTER_ALLOWED_URL_NAMES,
    _is_non_admin_teacher,
    _is_tester_teacher,
)
from core.models import FunFridayScheduledSend
from core.tasks import reset_tester_environment_task
from core.transactions import visible_students_for
from students.models import Student, Teacher

pytestmark = pytest.mark.django_db

APP = settings.APP_PATH_PREFIX


def _make_teacher(*, email, admin=False, tester=False, password="tester-pw-12345"):
    teacher = Teacher.objects.create(
        first_name="Tester" if tester else "Ada",
        last_name="Publico" if tester else "Admin",
        email=email,
        admin=admin,
        tester=tester,
        active=True,
    )
    teacher.ensure_user(password=password)
    return teacher


def _logged_in(client, teacher):
    """Both layers, exactly as a real login leaves them."""
    client.force_login(teacher.user)
    session = client.session
    session["is_authenticated"] = True
    session["username"] = teacher.first_name
    session.save()
    return client


@pytest.fixture(autouse=True)
def _testing_env(settings):
    """The tester role is inert outside testing (see `_is_tester_teacher`).

    Autouse because the suite runs as DEVELOPMENT, so without it every test here
    would assert against a deliberately disabled feature and pass vacuously.
    `TestAdminsAndTeachersAreUnaffected` overrides it back off where that is the
    thing under test.
    """
    settings.IS_TESTING_ENV = True


@pytest.fixture
def tester_teacher():
    return _make_teacher(email="tester@fiveaday.test", admin=False, tester=True)


@pytest.fixture
def tester_client(client, tester_teacher):
    return _logged_in(client, tester_teacher)


@pytest.fixture
def plain_teacher():
    return _make_teacher(email="plain@fiveaday.test", admin=False, tester=False)


# ─────────────────────────────────────────────────────────────────────────────
# 0 — THE ISOLATION GUARANTEE
#
# The tester role only ever WIDENS, and every hook that widens is reached
# through `_is_tester_teacher`, which is a constant False outside the testing
# environment and for any teacher whose row is not flagged. This section pins
# that the two pre-existing roles behave EXACTLY as they did before the role was
# added — it is the section to run first if anyone ever wonders whether the demo
# account changed something for the academy's real users.
# ─────────────────────────────────────────────────────────────────────────────


class TestAdminsAndTeachersAreUnaffected:
    """Every tester hook must short-circuit before it can change these two roles."""

    def test_the_only_divergent_input_requires_the_tester_flag(self):
        """The `is_admin_user` expression, proved exhaustively rather than argued.

        old: authenticated and not non_admin
        new: authenticated and (not non_admin or tester)

        They differ on exactly ONE of the eight input combinations, and it needs
        `non_admin=True` AND `tester=True` at once. An admin is never the first
        (`_is_non_admin_teacher` is False for them); a plain teacher is never the
        second. Neither can reach the row that changed.
        """
        divergent = [
            (auth, non_admin, tester)
            for auth in (False, True)
            for non_admin in (False, True)
            for tester in (False, True)
            if (auth and not non_admin) != (auth and (not non_admin or tester))
        ]
        assert divergent == [(True, True, True)]

    def test_the_predicate_is_inert_outside_the_testing_environment(self, tester_teacher, rf, settings):
        """Even a genuinely flagged row widens nothing in production or development.

        This is what makes the guarantee structural instead of a promise that no
        production row will ever carry the flag — a restored dump or a seed block
        copied between `.env` files would be enough to break a promise.
        """
        request = rf.get("/")
        request.user = tester_teacher.user

        settings.IS_TESTING_ENV = True
        assert _is_tester_teacher(request) is True

        for environment in ("production", "development"):
            settings.ENVIRONMENT = environment
            settings.IS_TESTING_ENV = False
            assert _is_tester_teacher(request) is False

    def test_seed_teachers_refuses_to_flag_a_row_outside_testing(self, monkeypatch, settings):
        """So production cannot even hold a tester row to begin with."""
        settings.IS_TESTING_ENV = False
        monkeypatch.setenv("TEACHER_SEED_1_FIRST_NAME", "Would")
        monkeypatch.setenv("TEACHER_SEED_1_LAST_NAME", "BeTester")
        monkeypatch.setenv("TEACHER_SEED_1_EMAIL", "would-be@fiveaday.test")
        monkeypatch.setenv("TEACHER_SEED_1_TESTER", "True")

        call_command("seed_teachers")

        assert Teacher.objects.get(email="would-be@fiveaday.test").tester is False

    # -- the admin ------------------------------------------------------------

    def test_an_admin_keeps_the_django_admin_flags(self):
        admin = _make_teacher(email="iso-admin@fiveaday.test", admin=True)
        assert admin.grants_django_admin is True
        admin.user.refresh_from_db()
        assert admin.user.is_staff is True
        assert admin.user.is_superuser is True

    def test_an_admin_still_renders_the_full_ui(self, client, site_config):
        admin = _make_teacher(email="iso-admin-ui@fiveaday.test", admin=True)
        response = _logged_in(client, admin).get(reverse("home"))
        assert response.context["is_admin_user"] is True
        assert response.context["is_tester_user"] is False

    def test_an_admin_still_keeps_the_qa_tools(self, settings):
        settings.IS_TESTING_ENV = True
        assert may_use_qa_tools(_make_teacher(email="iso-qa@fiveaday.test", admin=True)) is True

    @pytest.mark.parametrize(
        "url_name",
        ["home", "payments_list", "expenses_list", "reports_view", "management", "change_password"],
    )
    def test_an_admin_reaches_everything_it_reached_before(self, client, site_config, url_name):
        """Including `change_password`, which the TESTER is denied — the removal
        must apply to the tester's allowlist only, never to the real roles."""
        admin = _make_teacher(email=f"iso-{url_name}@fiveaday.test", admin=True)
        logged_in = _logged_in(client, admin)
        response = (
            logged_in.post(reverse(url_name)) if url_name == "change_password" else logged_in.get(reverse(url_name))
        )
        assert response.status_code != 403
        assert b"cuenta de pruebas" not in response.content.lower()

    def test_an_admin_is_never_scoped_by_the_student_filter(self, client, student, site_config):
        admin = _make_teacher(email="iso-scope@fiveaday.test", admin=True)
        request = _logged_in(client, admin).get(reverse("students_list")).wsgi_request
        assert visible_students_for(request, Student.objects.all()).filter(pk=student.pk).exists()

    # -- the plain teacher ----------------------------------------------------

    def test_a_plain_teacher_still_gets_the_trimmed_ui(self, client, plain_teacher, site_config):
        response = _logged_in(client, plain_teacher).get(reverse("home"))
        assert response.context["is_admin_user"] is False
        assert response.context["is_tester_user"] is False

    @pytest.mark.parametrize("url_name", ["change_password", "submit_support_ticket"])
    def test_a_plain_teacher_keeps_the_two_endpoints_removed_from_the_tester(
        self, client, plain_teacher, site_config, url_name
    ):
        """The narrowing is the tester's alone. If this ever fails, the removal
        leaked out of `TESTER_ALLOWED_URL_NAMES` and into the real role."""
        assert url_name in NON_ADMIN_ALLOWED_URL_NAMES
        response = _logged_in(client, plain_teacher).post(reverse(url_name), {}, follow=False)
        assert response.status_code != 403

    @pytest.mark.parametrize("url_name", ["payments_list", "expenses_list", "reports_view"])
    def test_a_plain_teacher_is_still_denied_what_it_was_always_denied(
        self, client, plain_teacher, site_config, url_name
    ):
        response = _logged_in(client, plain_teacher).get(reverse(url_name), follow=False)
        assert response.status_code in (302, 403)

    def test_a_plain_teacher_gets_the_ORIGINAL_refusal_wording(self, client, plain_teacher, site_config):
        """Layer 2 now computes a message. The plain-teacher branch must still be
        the deliberately vague original, not the friendlier tester one — that
        vagueness is what stops a prober learning which control refused them.
        """
        response = _logged_in(client, plain_teacher).get(reverse("payments_list"), follow=True)
        body = response.content.decode()
        assert "No tienes permiso para acceder a esa sección" in body
        assert "cuenta de pruebas" not in body.lower()

    def test_a_plain_teacher_is_still_scoped_to_its_own_groups(self, client, plain_teacher, student, site_config):
        request = _logged_in(client, plain_teacher).get(reverse("students_list")).wsgi_request
        # `student`'s group is not taught by this teacher, so it must not appear.
        assert not visible_students_for(request, Student.objects.all()).filter(pk=student.pk).exists()


# ─────────────────────────────────────────────────────────────────────────────
# 1 — the role sits between the other two
# ─────────────────────────────────────────────────────────────────────────────


def test_the_allowlist_is_a_strict_superset_of_the_plain_teacher_whitelist():
    """Minus exactly two names — the whole definition of the role, asserted.

    A tester that is not a superset would be a DIFFERENT role rather than a
    wider one, and the two removals are the only intentional narrowing.
    """
    removed = NON_ADMIN_ALLOWED_URL_NAMES - TESTER_ALLOWED_URL_NAMES
    assert removed == {"change_password", "submit_support_ticket"}
    assert len(TESTER_ALLOWED_URL_NAMES) > len(NON_ADMIN_ALLOWED_URL_NAMES)


@pytest.mark.parametrize("url_name", ["change_password", "submit_support_ticket"])
def test_two_endpoints_an_ordinary_teacher_may_use_are_denied_to_the_tester(url_name):
    """Pinned because this is the surprising direction and both are one-way.

    `change_password` would let one visitor lock out everyone who reads the
    published password next; `submit_support_ticket` delivers to a real inbox.
    Somebody tidying the allowlist would otherwise "fix" their absence.
    """
    assert url_name in NON_ADMIN_ALLOWED_URL_NAMES
    assert url_name not in TESTER_ALLOWED_URL_NAMES


@pytest.mark.parametrize(
    "url_name",
    ["create_teacher", "export_to_sheets", "drive_oauth_disconnect", "two_factor_setup", "testing_tools"],
)
def test_the_dangerous_admin_endpoints_stay_out_of_the_allowlist(url_name):
    assert url_name not in TESTER_ALLOWED_URL_NAMES


def test_the_tester_is_not_an_admin_in_the_database(tester_teacher):
    assert tester_teacher.admin is False
    assert tester_teacher.grants_django_admin is False
    tester_teacher.user.refresh_from_db()
    assert tester_teacher.user.is_staff is False
    assert tester_teacher.user.is_superuser is False


def test_ticking_admin_on_a_tester_row_still_withholds_the_django_admin(tester_teacher):
    """Belt and braces. A published credential must not be one careless
    checkbox in `/admin/` away from a Django superuser."""
    tester_teacher.admin = True
    tester_teacher.save()

    assert tester_teacher.grants_django_admin is False
    tester_teacher.user.refresh_from_db()
    assert tester_teacher.user.is_staff is False
    assert tester_teacher.user.is_superuser is False


def test_an_ordinary_admin_is_unaffected():
    teacher = _make_teacher(email="real-admin@fiveaday.test", admin=True, tester=False)
    assert teacher.grants_django_admin is True
    teacher.user.refresh_from_db()
    assert teacher.user.is_staff is True
    assert teacher.user.is_superuser is True


# ─────────────────────────────────────────────────────────────────────────────
# 2 — the predicate
# ─────────────────────────────────────────────────────────────────────────────


def test_the_predicate_identifies_the_tester(tester_teacher, rf):
    request = rf.get("/")
    request.user = tester_teacher.user
    assert _is_tester_teacher(request) is True
    # Still a restricted session: it is the allowlist that differs, not the tier.
    assert _is_non_admin_teacher(request) is True


def test_the_predicate_is_false_for_a_plain_teacher(plain_teacher, rf):
    request = rf.get("/")
    request.user = plain_teacher.user
    assert _is_tester_teacher(request) is False


def test_the_predicate_is_false_for_an_anonymous_request(rf):
    """It must never WIDEN on its own: an unresolvable session is an ordinary
    one, judged by the controls around it."""
    request = rf.get("/")
    request.user = AnonymousUser()
    assert _is_tester_teacher(request) is False


# ─────────────────────────────────────────────────────────────────────────────
# 3 — refusals
# ─────────────────────────────────────────────────────────────────────────────


def test_tester_cannot_reach_the_django_admin(tester_client):
    response = tester_client.get(f"{APP}/admin/", follow=False)
    assert response.status_code != 200


def test_tester_is_excluded_from_the_qa_tools(tester_teacher, settings):
    settings.IS_TESTING_ENV = True
    assert may_use_qa_tools(tester_teacher) is False

    ordinary = _make_teacher(email="qa-admin@fiveaday.test", admin=True, tester=False)
    assert may_use_qa_tools(ordinary) is True


def test_qa_dashboard_is_unreachable_for_the_tester(tester_client, settings):
    """Refused by the MIDDLEWARE (302), one layer before `qa_access_required`.

    Worth spelling out, because a plain teacher gets a 404 here — the QA gate's
    deliberate "this page does not exist". The tester is turned away earlier, by
    the allowlist, so the status differs while the outcome does not. Both gates
    are real: `may_use_qa_tools` is asserted separately above, and it is what
    still refuses if `testing_tools` were ever added to the allowlist.
    """
    settings.IS_TESTING_ENV = True
    response = tester_client.get(reverse("testing_tools"), follow=False)
    assert response.status_code in (302, 404)
    assert response.status_code != 200


@pytest.mark.parametrize(
    "url_name",
    ["change_password", "submit_support_ticket", "create_teacher", "export_to_sheets", "two_factor_setup"],
)
def test_denied_endpoints_refuse_the_tester_over_http(tester_client, url_name):
    """Both verbs, because the set mixes GET pages with POST-only endpoints and
    a 405 from `require_http_methods` would be a false pass."""
    url = reverse(url_name)
    responses = [tester_client.get(url, follow=False), tester_client.post(url, {}, follow=False)]
    assert any(r.status_code in (302, 403, 404) for r in responses), [r.status_code for r in responses]
    for response in responses:
        assert response.status_code != 200, f"{url_name} served 200 to the tester"


# ─────────────────────────────────────────────────────────────────────────────
# 4 — what the tester keeps (the point of the role)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url_name",
    ["home", "students_list", "payments_list", "expenses_list", "reports_view", "management", "apps"],
)
def test_tester_reaches_the_surface_worth_showing(tester_client, site_config, url_name):
    """These are the pages a plain teacher is denied and the demo exists to show.

    Each is also `@admin_required`, so this is what proves `_tester_may_reach`
    actually widens the decorator — without it the middleware would pass the
    request and the view would refuse it one layer later.
    """
    assert url_name in TESTER_ALLOWED_URL_NAMES
    assert tester_client.get(reverse(url_name)).status_code == 200


@pytest.mark.parametrize("url_name", ["payments_list", "expenses_list", "reports_view"])
def test_a_plain_teacher_is_still_denied_those_same_pages(client, plain_teacher, site_config, url_name):
    """The widening must apply to the TESTER and to nothing else."""
    logged_in = _logged_in(client, plain_teacher)
    assert logged_in.get(reverse(url_name), follow=False).status_code in (302, 403)


def test_tester_sees_the_full_shell(tester_client, site_config):
    """`is_admin_user` drives the sidebar. Allowlisting the URLs without this
    would leave the tester able to reach pages it has no navigation to."""
    body = tester_client.get(reverse("home")).content
    assert b"Cuenta de pruebas" in body
    assert reverse("payments_list").encode() in body


def test_an_ordinary_admin_never_sees_the_tester_banner(client, site_config):
    admin = _make_teacher(email="noban@fiveaday.test", admin=True, tester=False)
    assert b"Cuenta de pruebas" not in _logged_in(client, admin).get(reverse("home")).content


def test_tester_sees_every_student_not_just_its_own_groups(tester_client, student, site_config):
    """A tester is `admin=False`, so `visible_students_for` would scope it to the
    groups it teaches — which is none, making every page an empty roll."""
    # An explicit queryset, because the default is `students_on_the_roll()` —
    # which filters on a current enrollment and would make this assert the
    # fixture's enrolment state rather than the SCOPING this test is about.
    request = tester_client.get(reverse("students_list")).wsgi_request
    visible = visible_students_for(request, Student.objects.all())
    assert visible.filter(pk=student.pk).exists()


# ─────────────────────────────────────────────────────────────────────────────
# 5 — email containment
# ─────────────────────────────────────────────────────────────────────────────


def test_allowlist_passes_everything_through_when_unset(settings):
    """Unset must mean pass-through: in production this variable is never set,
    and a fail-closed default would silently stop the academy's real mail."""
    settings.EMAIL_ALLOWED_RECIPIENTS = []
    assert filter_allowed_recipients(["a@x.com", "b@y.com"]) == ["a@x.com", "b@y.com"]


def test_allowlist_keeps_exact_addresses_and_domain_suffixes(settings):
    settings.EMAIL_ALLOWED_RECIPIENTS = ["qa@example.com", "@allowed.test"]
    kept = filter_allowed_recipients(
        ["qa@example.com", "someone@allowed.test", "family@fiveaday.test", "other@example.com"]
    )
    assert kept == ["qa@example.com", "someone@allowed.test"]


def test_allowlist_is_case_insensitive(settings):
    """Addresses are typed by hand into .env files and into the app; a case
    mismatch swallowing the mail would be indistinguishable from an outage."""
    settings.EMAIL_ALLOWED_RECIPIENTS = ["qa@example.com"]
    assert filter_allowed_recipients(["QA@Example.COM"]) == ["QA@Example.COM"]


def test_a_fully_suppressed_email_is_never_sent(settings):
    settings.EMAIL_ALLOWED_RECIPIENTS = ["@allowed.test"]

    mail.outbox.clear()
    sent = email_service.send_email(
        template_name="happy_birthday",
        recipients=["nobody@fiveaday.test"],
        subject="Cumpleaños",
        context={"student_name": "Leo"},
    )
    # Reported as success: nothing FAILED, the policy did its job. Returning
    # False would show the operator a failure banner for a working control.
    assert sent is True
    assert mail.outbox == []


def test_a_partially_allowed_email_reaches_only_the_allowed_recipients(settings):
    settings.EMAIL_ALLOWED_RECIPIENTS = ["@allowed.test"]

    mail.outbox.clear()
    email_service.send_email(
        template_name="happy_birthday",
        recipients=["nobody@fiveaday.test", "qa@allowed.test"],
        subject="Cumpleaños",
        context={"student_name": "Leo"},
    )
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["qa@allowed.test"]


def test_tester_can_preview_a_mail_form_but_not_send_it(tester_client, site_config):
    """The ten forms serve preview and send from ONE url_name, split by a POST
    field — so the allowlist cannot separate them and the chokepoints must."""
    url = reverse("birthday_form")
    assert tester_client.get(url).status_code == 200

    # `preview` renders the template and touches nothing outside the request.
    preview = tester_client.post(url, {"action": "preview"})
    assert preview.status_code == 200
    assert b"html" in preview.content

    # `test_send` really sends — and `_test_send_recipients` falls back to
    # SUPPORT_EMAIL, so on a shared public account that fallback is the
    # maintainer's own inbox.
    mail.outbox.clear()
    sent = tester_client.post(url, {"action": "test_send"})
    assert sent.status_code == 200
    assert mail.outbox == []


# ─────────────────────────────────────────────────────────────────────────────
# 6 — the nightly repair
# ─────────────────────────────────────────────────────────────────────────────


def test_reset_refuses_to_run_in_production(settings):
    settings.ENVIRONMENT = "production"
    with pytest.raises(CommandError):
        call_command("reset_tester_environment", "--skip-seed")


def _seed_env(monkeypatch, teacher, password="the-published-password"):
    monkeypatch.setenv("TEACHER_SEED_1_FIRST_NAME", "Tester")
    monkeypatch.setenv("TEACHER_SEED_1_EMAIL", teacher.email)
    monkeypatch.setenv("TEACHER_SEED_1_PASSWORD", password)


def test_reset_restores_a_password_a_visitor_changed(tester_teacher, monkeypatch, site_config):
    """The failure this exists for: a changed password on a PUBLISHED credential
    is otherwise unrecoverable by any automated path."""
    _seed_env(monkeypatch, tester_teacher)
    tester_teacher.user.set_password("hijacked-by-a-visitor")
    tester_teacher.user.save()

    call_command("reset_tester_environment", "--skip-seed")

    tester_teacher.user.refresh_from_db()
    assert tester_teacher.user.check_password("the-published-password")


def test_reset_clears_a_second_factor_enrolled_on_the_tester(tester_teacher, monkeypatch, site_config):
    """2FA on a SHARED account locks out every future visitor at once."""
    _seed_env(monkeypatch, tester_teacher)
    tester_teacher.two_factor_enabled = True
    tester_teacher.two_factor_secret = "JBSWY3DPEHPK3PXP"
    tester_teacher.two_factor_backup_codes = ["hash"]
    tester_teacher.save()

    call_command("reset_tester_environment", "--skip-seed")

    tester_teacher.refresh_from_db()
    assert tester_teacher.two_factor_enabled is False
    assert tester_teacher.two_factor_secret == ""
    assert tester_teacher.two_factor_backup_codes == []


def test_reset_restores_prices_a_visitor_edited(tester_teacher, site_config):
    """`SiteConfiguration` survives `seed_testdata --reset`, so without this step
    a price edit — which the tester IS allowed to make — would persist for good."""
    assert "update_site_config" in TESTER_ALLOWED_URL_NAMES

    config = SiteConfiguration.get_config()
    config.full_time_monthly_fee = constants.FULL_TIME_MONTHLY_FEE + 100
    config.save()

    call_command("reset_tester_environment", "--skip-seed")

    config.refresh_from_db()
    assert config.full_time_monthly_fee == constants.FULL_TIME_MONTHLY_FEE


def test_reset_discards_announcements_queued_but_not_sent(tester_teacher, site_config):
    """Beat drains these at 14:30, long after the request that queued them — the
    one email side effect no request-scoped guard can reach."""
    FunFridayScheduledSend.objects.create(
        recipients=["familia@fiveaday.test"],
        day_name="Viernes",
        day_number=5,
        month="Junio",
        start_time="17:00",
        end_time="19:00",
        activity_description="Queued by a tester visitor",
        scheduled_for=timezone.now() + timedelta(days=1),
    )
    call_command("reset_tester_environment", "--skip-seed")
    assert FunFridayScheduledSend.objects.filter(sent_at__isnull=True).count() == 0


def test_nightly_task_is_a_no_op_outside_the_testing_environment(settings, monkeypatch):
    """Beat runs in DEVELOPMENT too. Without this gate the nightly entry would
    wipe every developer's local database at 07:30, silently."""
    settings.IS_TESTING_ENV = False
    called = []
    monkeypatch.setattr("core.tasks.call_command", lambda *a, **k: called.append(a))

    result = reset_tester_environment_task()

    assert result["status"] == "skipped"
    assert called == []
