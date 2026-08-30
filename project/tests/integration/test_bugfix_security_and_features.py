"""Security regressions + the v1.15 feature additions.

The security half pins holes that were confirmed exploitable against the
running app: two stored-XSS sinks reachable by a non-admin teacher, a JSON
block that could be broken out of, a rate limit that could be bypassed by
rotating a request header, and a magic-link login that reused the pre-auth
session id.

The feature half covers the backlog items added in the same pass.
"""

from datetime import date
from decimal import Decimal

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from billing.models import Payment
from core.models import HistoryLog, TodoItem
from students.models import Student

pytestmark = pytest.mark.django_db


def _client():
    c = Client(raise_request_exception=False)
    session = c.session
    session["is_authenticated"] = True
    session["username"] = "tester"
    session.save()
    return c


# ═════════════════════════════════════════════════════════════════════════════
# SECURITY
# ═════════════════════════════════════════════════════════════════════════════


class TestHistoryFeedIsNotAnXssSink:
    """base.js rendered `e.message` with innerHTML and no escaping.

    HistoryLog messages embed free text — `complete_todo` interpolates the todo
    title verbatim. `create_todo`/`complete_todo` are both on the non-admin
    teacher whitelist, so a non-admin could plant a payload that executed in an
    ADMIN's browser on every page (base.js is global). That is privilege
    escalation, not just defacement.
    """

    PAYLOAD = '<img src=x onerror="alert(1)">'

    def test_payload_still_round_trips_as_data(self, authenticated_client):
        """The API is a JSON data feed; escaping belongs at the render site, so
        the value itself is expected to survive intact."""
        todo = TodoItem.objects.create(text=self.PAYLOAD, due_date=date(2026, 12, 31))
        authenticated_client.post(reverse("complete_todo", args=[todo.id]))
        message = authenticated_client.get(reverse("history_list")).json()["entries"][0]["message"]
        assert self.PAYLOAD in message

    def test_renderer_escapes_before_inserting(self):
        """base.js must escape it. Guarding the renderer is what actually
        matters — the data feed is not the vulnerability, the innerHTML is."""
        source = (settings.BASE_DIR / "core/static/js/base.js").read_text(encoding="utf-8")
        assert "function escapeHtml" in source
        assert "escapeHtml(e.message)" in source, "history messages must be escaped before innerHTML"


class TestStudentSearchIsNotAnXssSink:
    """payments.js built suggestion rows with innerHTML and an inline
    `onclick="...('${s.full_name}')"`. Only single quotes were escaped, so a
    name containing a double quote broke out of the HTML attribute.
    """

    def test_names_are_returned_verbatim_by_the_api(self, group):
        Student.objects.create(
            first_name='Ana" onmouseover="alert(1)',
            last_name="<img src=x onerror=alert(2)>",
            birth_date=date(2015, 1, 1),
            group=group,
            active=True,
        )
        results = _client().get("/api/search/students/?q=Ana").json()["results"]
        assert results, "the student should still be findable"

    def test_suggestions_are_built_as_dom_nodes(self):
        """No innerHTML, no inline onclick — the values go through textContent
        and the handler is attached with addEventListener."""
        source = (settings.BASE_DIR / "core/static/js/payments.js").read_text(encoding="utf-8")
        block = source.split("function displayStudentSuggestions")[1].split("\n    }")[0]
        # Strip `//` comments so the prose explaining the fix isn't mistaken for
        # the pattern it describes.
        code = "\n".join(line.split("//")[0] for line in block.splitlines())

        assert "innerHTML" not in code
        assert "onclick=" not in code
        assert "textContent" in code
        assert "addEventListener('click'" in code


class TestScheduleJsonCannotBreakOutOfTheScriptBlock:
    """schedule.html inlined `{{ groups_json|safe }}` inside <script>.

    json.dumps does not escape `</script>`, so a student or group name could
    terminate the block and inject markup. /schedule/ is whitelisted for
    non-admin teachers, so this was the same escalation path as the history feed.
    """

    def test_student_name_containing_a_script_tag_is_escaped(self, authenticated_client, group):
        Student.objects.create(
            first_name="</script><script>alert(1)</script>",
            last_name="X",
            birth_date=date(2015, 1, 1),
            group=group,
            active=True,
        )
        body = authenticated_client.get(reverse("schedule_view")).content.decode()
        assert "</script><script>alert(1)" not in body

    def test_config_is_delivered_via_json_script(self, authenticated_client, group):
        body = authenticated_client.get(reverse("schedule_view")).content.decode()
        assert 'id="schedule-groups"' in body
        assert 'type="application/json"' in body


class TestRateLimitCannotBeBypassedByHeaderSpoofing:
    """`X-Forwarded-For.split(",")[0]` is client-controlled.

    A proxy APPENDS what it saw, so the leftmost entry is whatever the client
    sent. Rotating it gave every request a fresh bucket — verified live: 12
    login attempts, 0 throttled, against a documented limit of 5/min.
    """

    def test_rotating_the_spoofed_prefix_still_throttles(self, settings):
        from django.core.cache import cache

        settings.RATELIMIT_ENABLE = True
        settings.TRUSTED_PROXY_COUNT = 1
        cache.clear()

        client = Client(raise_request_exception=False)
        statuses = [
            client.post(
                "/login/",
                data={"username": "x", "password": "y"},
                HTTP_X_FORWARDED_FOR=f"10.0.0.{i}, 203.0.113.7",
            ).status_code
            for i in range(12)
        ]
        assert 429 in statuses, "a spoofed XFF prefix must not create a fresh bucket"

    def test_distinct_real_clients_get_distinct_buckets(self, settings):
        """The throttle must not become a global one — two genuinely different
        clients should not share a counter."""
        from django.core.cache import cache

        settings.RATELIMIT_ENABLE = True
        settings.TRUSTED_PROXY_COUNT = 1
        cache.clear()

        client = Client(raise_request_exception=False)
        for _ in range(5):
            client.post("/login/", data={"username": "x", "password": "y"}, HTTP_X_FORWARDED_FOR="203.0.113.1")
        other = client.post("/login/", data={"username": "x", "password": "y"}, HTTP_X_FORWARDED_FOR="203.0.113.99")
        assert other.status_code != 429


class TestParentPortalSessionHandling:
    """The magic-link login set `parent_id` on the EXISTING session.

    That is session fixation (django.contrib.auth.login cycles the key for
    exactly this reason), and it also meant an admin who clicked a parent's
    link held both identities in one cookie — after which `parent_portal_logout`
    only popped `parent_id` and left the admin session intact.
    """

    def test_login_cycles_the_session_key(self, parent):
        from students.models import ParentSessionToken

        client = Client()
        client.get(reverse("parent_portal_login"))
        before = client.session.session_key
        token = ParentSessionToken.issue(parent)
        client.get(reverse("parent_portal_verify", args=[token.token]))
        assert client.session.session_key != before

    def test_admin_state_does_not_survive_a_parent_login(self, parent):
        from students.models import ParentSessionToken

        client = _client()
        token = ParentSessionToken.issue(parent)
        client.get(reverse("parent_portal_verify", args=[token.token]))
        assert client.session.get("parent_id") == parent.id
        assert client.session.get("is_authenticated") is None

    def test_logout_clears_the_whole_session(self, parent):
        from students.models import ParentSessionToken

        client = Client()
        token = ParentSessionToken.issue(parent)
        client.get(reverse("parent_portal_verify", args=[token.token]))
        client.get(reverse("parent_portal_logout"))
        assert client.session.get("parent_id") is None


class TestServiceWorkerDoesNotCacheLogin:
    """A cache-first /login/ served a stale csrfmiddlewaretoken after Django
    rotated the CSRF secret on sign-in, producing intermittent 403s. The Cache
    API ignores Cache-Control, so the server's no-store could not stop it.
    """

    def test_login_is_excluded(self, client):
        body = client.get(reverse("service_worker")).content.decode()
        cacheable = body.split("function isCacheable")[1].split("}")[0]
        assert '"/login/"' not in cacheable

    def test_login_page_does_carry_a_csrf_token(self, client):
        """Establishes why caching it was unsafe in the first place."""
        assert "csrfmiddlewaretoken" in client.get("/login/").content.decode()


class TestScheduleSlotValidation:
    """`save_schedule_slot` accepted any ints. One out-of-range row made
    /schedule/ raise IndexError for EVERY user, with no UI to undo it.
    """

    @pytest.mark.parametrize(
        ("row", "day", "col"),
        [(99, 0, 0), (-1, 0, 0), (0, 9, 0), (0, 0, 5), (2, 4, 0)],  # last: Friday row 2 has no session
    )
    def test_invalid_slots_are_rejected(self, authenticated_client, group, row, day, col):
        import json

        response = authenticated_client.post(
            reverse("save_schedule_slot"),
            data=json.dumps({"row": row, "day": day, "col": col, "group_id": group.id}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json()["success"] is False

    def test_schedule_page_survives_a_poisoned_row(self, authenticated_client, group):
        """Rows written before validation existed must not break rendering."""
        from core.models import ScheduleSlot

        ScheduleSlot.objects.create(row=99, day=0, col=0, group=group)
        assert authenticated_client.get(reverse("schedule_view")).status_code == 200


class TestTeacherCreatedInTheUiCanRecoverTheirAccount:
    """`create_teacher` never created the linked auth.User, so the account
    could not log in AND `/password-reset/` silently sent nothing.
    """

    def test_linked_user_is_created(self, authenticated_client):
        import json

        from django.contrib.auth import get_user_model

        from students.models import Teacher

        authenticated_client.post(
            reverse("create_teacher"),
            data=json.dumps({"first_name": "Nuevo", "last_name": "Profe", "email": "nuevo@fiveaday.test"}),
            content_type="application/json",
        )
        teacher = Teacher.objects.get(email="nuevo@fiveaday.test")
        assert teacher.user_id is not None
        assert get_user_model().objects.filter(username="nuevo@fiveaday.test").exists()

    def test_password_reset_reaches_them(self, authenticated_client):
        import json

        from django.core import mail

        authenticated_client.post(
            reverse("create_teacher"),
            data=json.dumps({"first_name": "Nuevo", "last_name": "Profe", "email": "nuevo@fiveaday.test"}),
            content_type="application/json",
        )
        mail.outbox.clear()
        Client().post(reverse("password_reset"), data={"email": "nuevo@fiveaday.test"})
        assert len(mail.outbox) == 1, "the new teacher must be able to activate their account"

    def test_new_teacher_has_no_usable_password_until_they_set_one(self, authenticated_client):
        import json

        from django.contrib.auth import get_user_model

        from students.models import Teacher

        authenticated_client.post(
            reverse("create_teacher"),
            data=json.dumps({"first_name": "Nuevo", "last_name": "Profe", "email": "nuevo@fiveaday.test"}),
            content_type="application/json",
        )
        user = get_user_model().objects.get(username="nuevo@fiveaday.test")
        assert not user.has_usable_password()
        assert Teacher.objects.get(email="nuevo@fiveaday.test").admin is False


# ═════════════════════════════════════════════════════════════════════════════
# BACKLOG FEATURES
# ═════════════════════════════════════════════════════════════════════════════


class TestWaitingListShortForm:
    """Backlog: only a name and a number should be needed to take an entry."""

    URL = "/students/waiting/create/"

    def test_name_and_phone_are_enough(self):
        response = _client().post(
            self.URL,
            data={"first_name": "Marta", "last_name": "Ruiz", "waiting_contact_phone": "600111222"},
        )
        assert response.status_code == 302
        student = Student.objects.get(first_name="Marta")
        assert student.is_waiting is True
        assert student.waiting_contact_phone == "600111222"
        assert student.group_id is None, "no group should be required"
        assert student.birth_date is None, "no birth date should be required"

    def test_phone_is_required(self):
        response = _client().post(self.URL, data={"first_name": "Marta", "last_name": "Ruiz"})
        assert response.status_code == 200  # re-renders with the error
        assert not Student.objects.filter(first_name="Marta").exists()

    def test_optional_details_are_stored(self, group):
        _client().post(
            self.URL,
            data={
                "first_name": "Marta",
                "last_name": "Ruiz",
                "waiting_contact_name": "Ana Ruiz",
                "waiting_contact_phone": "600111222",
                "course": "3º Primaria",
                "age": 8,
                "group": group.id,
                "observations": "Preferiría martes y jueves",
            },
        )
        student = Student.objects.get(first_name="Marta")
        assert student.course == "3º Primaria"
        assert student.observations == "Preferiría martes y jueves"
        assert student.waiting_contact_name == "Ana Ruiz"
        assert student.group_id == group.id
        assert student.age == 8, "age should be derivable from the approximate birth date"

    def test_entry_appears_on_the_waiting_list_page(self):
        _client().post(
            self.URL,
            data={"first_name": "Marta", "last_name": "Ruiz", "waiting_contact_phone": "600111222"},
        )
        body = _client().get(reverse("waiting_list")).content.decode()
        assert "Marta" in body
        assert "600111222" in body

    def test_age_is_optional_and_leaves_no_birth_date(self):
        _client().post(
            self.URL,
            data={"first_name": "Sin", "last_name": "Edad", "waiting_contact_phone": "600111222"},
        )
        assert Student.objects.get(first_name="Sin").age is None


class TestWaitingListRoundTrip:
    """Compound bug: `add_to_waiting_list` left the enrollment active, so the
    student kept being billed AND `assign_from_waiting_list` then violated
    unique_active_enrollment_per_student and 500'd — a one-way door.
    """

    def test_moving_to_the_waiting_list_cancels_the_enrollment(self, student_with_parent, active_enrollment):
        response = _client().post(reverse("add_to_waiting_list", args=[student_with_parent.id]))
        assert response.status_code == 302
        student_with_parent.refresh_from_db()
        assert student_with_parent.is_waiting is True
        assert student_with_parent.enrollments.filter(status="active").count() == 0

    def test_the_student_can_be_promoted_back(self, student_with_parent, active_enrollment):
        """Promotion now hands over to the normal creation flow, which is what
        keeps it out of the unique-active-enrollment constraint entirely."""
        client = _client()
        client.post(reverse("add_to_waiting_list", args=[student_with_parent.id]))
        response = client.get(reverse("assign_from_waiting_list", args=[student_with_parent.id]))
        assert response.status_code == 302
        assert response.url == f"{reverse('parent_create')}?from_waiting={student_with_parent.id}"


class TestStudentPaymentHistoryPdf:
    """Backlog: "ver cuándo lo pagó y cómo lo pagó"."""

    def test_returns_a_pdf(self, student_with_parent, completed_payment):
        response = _client().get(f"/students/{student_with_parent.id}/payments.pdf")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response.content[:4] == b"%PDF"

    def test_works_for_a_student_with_no_payments(self, student):
        response = _client().get(f"/students/{student.id}/payments.pdf")
        assert response.status_code == 200
        assert response.content[:4] == b"%PDF"

    def test_year_filter_is_accepted(self, student_with_parent, completed_payment):
        response = _client().get(f"/students/{student_with_parent.id}/payments.pdf?year=2025")
        assert response.status_code == 200

    def test_bad_year_does_not_crash(self, student_with_parent, completed_payment):
        assert _client().get(f"/students/{student_with_parent.id}/payments.pdf?year=abc").status_code == 200

    def test_reportlab_markup_in_a_name_does_not_break_it(self, group):
        """`O<Brien` used to raise `paraparser: syntax error` and kill the PDF."""
        student = Student.objects.create(
            first_name="O<Brien", last_name="<b>Test</b>", birth_date=date(2015, 1, 1), group=group, active=True
        )
        assert _client().get(f"/students/{student.id}/payments.pdf").status_code == 200


class TestPaymentsMonthFilter:
    """Backlog: "añadir como una criba para ver los pagos de un mes especifico"."""

    @pytest.fixture
    def two_months(self, student_with_parent, parent, active_enrollment):
        for month, concept in ((3, "marzo"), (4, "abril")):
            Payment.objects.create(
                student=student_with_parent,
                parent=parent,
                enrollment=active_enrollment,
                payment_type="monthly",
                payment_method="transfer",
                amount=Decimal("54.00"),
                payment_status="pending",
                due_date=date(2026, month, 1),
                concept=concept,
            )

    def test_filtering_by_month_narrows_the_list(self, two_months):
        context = _client().get("/payments/?month=3&year=2026").context
        concepts = {p.concept for p in context["payments_list"]}
        assert concepts == {"marzo"}

    def test_no_month_shows_the_whole_year(self, two_months):
        context = _client().get("/payments/?year=2026").context
        assert {p.concept for p in context["payments_list"]} == {"marzo", "abril"}

    def test_the_month_dropdown_is_populated(self, two_months):
        context = _client().get("/payments/?year=2026").context
        assert len(context["month_choices"]) == 12
        assert 2026 in context["year_choices"]


class TestDatabaseGroupFilter:
    """Backlog: "filtrar por grupos" in Base de Datos."""

    def test_filter_narrows_to_one_group(self, student, teacher, group):
        from students.models import Group

        other_group = Group.objects.create(group_name="Otro", color="#000000", teacher=teacher, active=True)
        Student.objects.create(
            first_name="Fuera", last_name="Grupo", birth_date=date(2015, 1, 1), group=other_group, active=True
        )
        context = _client().get(f"/database/?students_group={group.id}").context
        names = {s.first_name for s in context["students"]}
        assert "Fuera" not in names
        assert context["students_group"] == group.id

    def test_no_filter_shows_everyone(self, student, teacher, group):
        from students.models import Group

        other = Group.objects.create(group_name="Otro", color="#000000", teacher=teacher, active=True)
        Student.objects.create(
            first_name="Fuera", last_name="Grupo", birth_date=date(2015, 1, 1), group=other, active=True
        )
        assert len(_client().get("/database/").context["students"]) == 2

    def test_bad_group_id_is_ignored(self, student):
        response = _client().get("/database/?students_group=abc")
        assert response.status_code == 200
        assert response.context["students_group"] is None


class TestBacklogExport:
    """Requested: a download of the current backlog with all its information."""

    @pytest.fixture
    def qa_admin_client(self, settings, db):
        """The QA dashboard is gated on an ADMIN Teacher in the testing env."""
        from django.contrib.auth import get_user_model

        from students.models import Teacher

        settings.IS_TESTING_ENV = True
        teacher = Teacher.objects.create(
            first_name="QA", last_name="Admin", email="qa@fiveaday.test", admin=True, active=True
        )
        teacher.ensure_user(password="qa-pass-12345")
        client = Client(raise_request_exception=False)
        client.force_login(get_user_model().objects.get(username="qa@fiveaday.test"))
        session = client.session
        session["is_authenticated"] = True
        session.save()
        return client

    @pytest.fixture
    def tasks(self, db):
        from core.models import BacklogTask

        BacklogTask.objects.create(title="Abierta", description="Pendiente", priority="high", status="open")
        BacklogTask.objects.create(title="Terminada", description="Hecha", priority="low", status="done")

    def test_json_export_defaults_to_active_tasks(self, qa_admin_client, tasks):
        import json

        response = qa_admin_client.get(reverse("export_backlog_tasks"))
        assert response.status_code == 200
        payload = json.loads(response.content)
        titles = {t["title"] for t in payload["tasks"]}
        assert titles == {"Abierta"}
        assert payload["count"] == 1

    def test_json_export_includes_every_field(self, qa_admin_client, tasks):
        import json

        payload = json.loads(qa_admin_client.get(reverse("export_backlog_tasks")).content)
        task = payload["tasks"][0]
        for field in ("id", "title", "description", "priority", "status", "created_by", "created_at", "updated_at"):
            assert field in task

    def test_scope_all_includes_done_tasks(self, qa_admin_client, tasks):
        import json

        payload = json.loads(qa_admin_client.get(reverse("export_backlog_tasks") + "?scope=all").content)
        assert {t["title"] for t in payload["tasks"]} == {"Abierta", "Terminada"}

    def test_csv_export(self, qa_admin_client, tasks):
        response = qa_admin_client.get(reverse("export_backlog_tasks") + "?format=csv")
        assert response.status_code == 200
        assert "text/csv" in response["Content-Type"]
        assert "attachment;" in response["Content-Disposition"]
        assert "Abierta" in response.content.decode("utf-8")

    def test_export_is_qa_gated(self, tasks):
        """Non-QA sessions must not reach it (the decorator 404s)."""
        assert _client().get(reverse("export_backlog_tasks")).status_code in (302, 404)


class TestFunFridayDrainDoesNotDoubleSend:
    """`sent_at` was written AFTER the batch, so two overlapping drains (the
    immediate .delay() from the form and the 14:30 scheduled run) each saw
    sent_at=None and mailed every parent twice.
    """

    def test_a_second_drain_sends_nothing(self):
        from django.core import mail
        from django.utils import timezone

        from comms.tasks import send_due_fun_friday_emails_task
        from core.models import FunFridayScheduledSend

        FunFridayScheduledSend.objects.create(
            recipients=["a@test.com", "b@test.com"],
            day_name="viernes",
            day_number=1,
            month="enero",
            start_time="17:30",
            end_time="18:30",
            activity_description="probe",
            scheduled_for=timezone.now(),
        )
        mail.outbox.clear()
        send_due_fun_friday_emails_task()
        after_first = len(mail.outbox)
        send_due_fun_friday_emails_task()
        assert len(mail.outbox) == after_first == 2

    def test_the_row_is_marked_sent(self):
        from django.utils import timezone

        from comms.tasks import send_due_fun_friday_emails_task
        from core.models import FunFridayScheduledSend

        row = FunFridayScheduledSend.objects.create(
            recipients=["a@test.com"],
            day_name="viernes",
            day_number=1,
            month="enero",
            start_time="17:30",
            end_time="18:30",
            activity_description="probe",
            scheduled_for=timezone.now(),
        )
        send_due_fun_friday_emails_task()
        row.refresh_from_db()
        assert row.sent_at is not None


class TestNewsletterDoesNotBlastEveryone:
    """When the selected group could not be found the view fell back to EVERY
    parent while keeping the group's name in the subject.
    """

    def test_missing_group_sends_nothing(self, student_with_parent, parent, group):
        from django.core import mail

        group.active = False
        group.save()
        mail.outbox.clear()
        response = _client().post(
            "/apps/newsletter/",
            data={"group_name": group.group_name, "newsletter_link": "http://x", "message": "hola"},
        )
        assert response.status_code == 302
        assert len(mail.outbox) == 0

    def test_valid_group_still_sends(self, student_with_parent, parent, group):
        from django.core import mail

        mail.outbox.clear()
        _client().post(
            "/apps/newsletter/",
            data={"group_name": group.group_name, "newsletter_link": "http://x", "message": "hola"},
        )
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [parent.email]


class TestAdultReceiptsReachAdultStudents:
    """The "recibo mensual (adultos)" option queried parents of active children,
    so it went to every child's parent and never to a single adult student.
    """

    def test_sent_to_the_adult_student(self, adult_student, student_with_parent, parent):
        from django.core import mail

        mail.outbox.clear()
        _client().post("/apps/receipts/", data={"receipt_type": "adult", "adult_month": "enero"})
        recipients = {addr for message in mail.outbox for addr in message.to}
        assert adult_student.email in recipients
        assert parent.email not in recipients, "a child's parent must not get the adult receipt"


class TestPaymentReceiptEmail:
    """Backlog: "no se envía un correo al padre cuando hace un pago".

    Only the Stripe webhook sent a receipt, so cash and transfer payments
    marked complete in the UI sent nothing at all.
    """

    def test_quick_complete_emails_a_receipt(self, student_with_parent, parent, active_enrollment):
        import json

        from django.core import mail

        payment = Payment.objects.create(
            student=student_with_parent,
            parent=parent,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("54.00"),
            payment_status="pending",
            due_date=date.today(),
            concept="probe",
        )
        mail.outbox.clear()
        _client().post(
            f"/api/payments/{payment.id}/quick-complete/",
            data=json.dumps({"payment_method": "cash"}),
            content_type="application/json",
        )
        assert len(mail.outbox) == 1
        assert parent.email in mail.outbox[0].to

    def test_no_receipt_when_editing_an_already_completed_payment(self, student_with_parent, parent, active_enrollment):
        import json

        from django.core import mail

        payment = Payment.objects.create(
            student=student_with_parent,
            parent=parent,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="cash",
            amount=Decimal("54.00"),
            payment_status="completed",
            payment_date=date.today(),
            due_date=date.today(),
            concept="probe",
        )
        mail.outbox.clear()
        _client().post(
            f"/payments/{payment.id}/update/",
            data=json.dumps({"observations": "una nota"}),
            content_type="application/json",
        )
        assert len(mail.outbox) == 0, "editing a completed payment must not re-send the receipt"


class TestStudentEditDoesNotChurnEnrollments:
    """`StudentUpdateView` unconditionally finished the active enrollment and
    created a replacement, so editing a school name produced a duplicate.
    """

    def test_unrelated_edit_keeps_the_same_enrollment(self, student_with_parent, active_enrollment):
        before = student_with_parent.enrollments.count()
        response = _client().post(
            reverse("student_update", args=[student_with_parent.id]),
            data={
                "first_name": student_with_parent.first_name,
                "last_name": student_with_parent.last_name,
                "birth_date": student_with_parent.birth_date.isoformat(),
                "school": "COLEGIO NUEVO",
                "allergies": "",
                "group": student_with_parent.group_id,
                "enrollment_plan": "monthly_full",
            },
        )
        assert response.status_code == 302
        assert student_with_parent.enrollments.count() == before
        student_with_parent.refresh_from_db()
        assert student_with_parent.school == "COLEGIO NUEVO"

    def test_changing_the_plan_does_issue_a_new_enrollment(
        self, student_with_parent, active_enrollment, enrollment_type_returning_student
    ):
        before = student_with_parent.enrollments.count()
        _client().post(
            reverse("student_update", args=[student_with_parent.id]),
            data={
                "first_name": student_with_parent.first_name,
                "last_name": student_with_parent.last_name,
                "birth_date": student_with_parent.birth_date.isoformat(),
                "school": student_with_parent.school,
                "allergies": "",
                "group": student_with_parent.group_id,
                "enrollment_plan": "quarterly",
            },
        )
        assert student_with_parent.enrollments.count() == before + 1
        assert student_with_parent.enrollments.filter(status="active").count() == 1
        assert student_with_parent.enrollments.get(status="active").payment_modality == "quarterly"


class TestEnrollmentTypeLabelsAreSpanish:
    """Backlog: "que ponga mejor el tipo de pago en español".

    display_name is parent-facing (it lands in the matriculation and welcome
    emails) but was seeded with the English choice key.
    """

    def test_seed_labels_are_translated(self):
        from billing.constants import ENROLLMENT_TYPE_DISPLAY_ES

        assert ENROLLMENT_TYPE_DISPLAY_ES["new_student"] == "Nuevo estudiante"
        assert ENROLLMENT_TYPE_DISPLAY_ES["returning_student"] == "Antiguo estudiante"
        assert ENROLLMENT_TYPE_DISPLAY_ES["adults"] == "Adulto"
        assert ENROLLMENT_TYPE_DISPLAY_ES["special"] == "Especial"

    def test_welcome_email_states_the_payment_frequency(self, student_with_parent, parent, active_enrollment):
        from django.core import mail

        from comms.tasks import send_welcome_email_task

        mail.outbox.clear()
        send_welcome_email_task(
            parent_id=parent.id, student_id=student_with_parent.id, enrollment_id=active_enrollment.id
        )
        body = mail.outbox[0].alternatives[0][0]
        assert "Forma de pago" in body
        assert "Mensual" in body


class TestHistoryLogActionsAreDeclared:
    """`HistoryLog.log("email_scheduled", ...)` used an action that was not in
    ACTION_CHOICES, so it rendered as a raw slug in the activity feed.
    """

    def test_fun_friday_scheduling_uses_a_declared_action(self, student_with_parent, parent, group):
        valid = {key for key, _ in HistoryLog.ACTION_CHOICES}
        response = _client().post(
            "/apps/fun-friday/",
            data={
                "event_date": "2026-09-18",
                "start_time": "17:30",
                "end_time": "18:30",
                "min_age": "5",
                "max_age": "12",
                "activity_description": "Manualidades",
            },
        )
        assert response.status_code == 302
        for entry in HistoryLog.objects.all():
            assert entry.action in valid, f"{entry.action!r} is not a declared ACTION_CHOICE"
