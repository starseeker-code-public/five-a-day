"""Admin behaviour: what it renders, and what it refuses to save.

`*/admin.py` is excluded from coverage (`pyproject.toml [tool.coverage.run]`),
so nothing else in the suite executes any of this — which is how a
`format_html()` call with no arguments once took `/admin/billing/enrollment/`
down in production. `test_admin_views.py` smoke-tests that every view renders;
this file covers the two things that needed rows and POSTs to find:

* `list_display` callables only run when the changelist has data, and the rows
  that break them are the awkward ones (a payment with no parent, a student
  with no group, a schedule slot outside the grid);
* several admins would happily SAVE things the rest of the app rejects, or
  expose things it deliberately hides.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.urls import reverse

from billing.admin import PaymentAdmin
from billing.models import Enrollment, EnrollmentType, Expense, Payment
from core.audit_models import AuditLog
from core.models import BacklogTask, Feature, FunFridayAttendance, HistoryLog, ScheduleSlot, TodoItem
from students.models import Group, Parent, Student, StudentParent, Teacher

pytestmark = pytest.mark.django_db

# The payload from core.utils.csv_safe's docstring: openpyxl and Excel both
# treat a leading "=" as a formula, and a teacher can put one in a name.
HOSTILE = '=HYPERLINK("http://evil/","x")'


@pytest.fixture
def admin_client_(client):
    """Superuser session for both layers of auth.

    `force_login` satisfies django.contrib.admin; the `is_authenticated`
    session key satisfies `SimpleAuthMiddleware`, which gates `/admin/` too and
    would otherwise redirect every request to `/login/`.
    """
    user = User.objects.create_superuser("admin-hard", "admin-hard@example.com", "unused")
    client.force_login(user)
    session = client.session
    session["is_authenticated"] = True
    session["username"] = user.username
    session.save()
    return client


@pytest.fixture
def rich(site_config, enrollment_type_new_student, enrollment_type_special):
    """One row of every shape the app can actually produce.

    Empty tables hide exactly the bugs this file exists to catch: a
    `list_display` callable is never called without rows.
    """
    teacher = Teacher.objects.create(
        first_name="Ada", last_name="Admin", email="ada@example.com", admin=True, active=True
    )
    group = Group.objects.create(group_name="B2 Martes", teacher=teacher, max_students=8, active=True)
    uncapped = Group.objects.create(group_name="Sin tope", teacher=teacher, max_students=0, active=True)

    parent = Parent.objects.create(
        first_name="Rosa", last_name="Gil", dni="11111111H", phone="600111222", email="rosa@example.com"
    )
    kid = Student.objects.create(first_name="Leo", last_name="Gil", group=group, birth_date=date(2015, 4, 2))
    StudentParent.objects.create(student=kid, parent=parent)
    adult = Student.objects.create(first_name="Marta", last_name="Ruiz", is_adult=True, email="m@example.com")
    Student.objects.create(first_name="Nadie", last_name="", group=None, birth_date=None)
    Student.objects.create(first_name="Espera", last_name="Cola", is_waiting=True, waiting_priority=True)
    hostile = Student.objects.create(first_name=HOSTILE, last_name="Injection", group=group)

    enr = Enrollment.objects.create(
        student=kid,
        enrollment_type=enrollment_type_new_student,
        enrollment_period_start=date(2025, 9, 15),
        enrollment_period_end=date(2026, 6, 27),
        academic_year="2025-2026",
        schedule_type="full_time",
        payment_modality="monthly",
        enrollment_amount=Decimal("54.00"),
        discount_percentage=Decimal("0.00"),
        final_amount=Decimal("54.00"),
        status="active",
        enrollment_date=date(2025, 9, 1),
    )
    for offset, status in enumerate(["pending", "completed", "cancelled", "failed", "refunded"]):
        Payment.objects.create(
            student=kid,
            parent=parent,
            enrollment=enr,
            payment_type="monthly",
            payment_method="transfer",
            amount=Decimal("54.00"),
            payment_status=status,
            due_date=date.today() - timedelta(days=30 - offset),
            payment_date=date.today() - timedelta(days=20) if status == "completed" else None,
            concept=f"Mensualidad {status}",
        )
    # An adult student has no guardian: Payment.parent is legitimately NULL, and
    # `parent_link` in the changelist has to survive it.
    Payment.objects.create(
        student=adult,
        parent=None,
        enrollment=None,
        payment_type="other",
        payment_method="cash",
        amount=Decimal("1.00"),
        payment_status="pending",
        due_date=date.today(),
        concept=HOSTILE,
        reference_number=HOSTILE,
        observations=HOSTILE,
    )
    Payment.objects.create(
        student=hostile,
        parent=None,
        enrollment=None,
        payment_type="enrollment",
        payment_method="card",
        amount=Decimal("40.00"),
        payment_status="pending",
        due_date=date.today(),
        concept="Matrícula",
    )

    Expense.objects.create(description="Alquiler", category="rent", amount=Decimal("500.00"), expense_date=date.today())
    ScheduleSlot.objects.create(row=0, day=0, col=0, group=group)
    ScheduleSlot.objects.create(row=1, day=4, col=1, group=uncapped)
    HistoryLog.objects.create(action="student_enrolled", message=HOSTILE, icon="school")
    TodoItem.objects.create(text="Llamar a Rosa", due_date=date.today() - timedelta(days=2))
    FunFridayAttendance.objects.create(student=kid, date=date.today())
    AuditLog.objects.create(action="update", model="Student", object_id="1", object_label="Leo", actor_label="ada")
    feature = Feature.objects.create(title="Portal", description="x", status="in_progress", deadline=date(2020, 1, 1))
    BacklogTask.objects.create(title="Bug", description="y", priority="high", status="pending", feature=feature)
    return {"group": group, "parent": parent, "student": kid, "enrollment": enr, "teacher": teacher}


class TestEveryAdminViewRendersWithRealRows:
    """The smoke suite runs against empty tables; these run against populated
    ones, which is the only way `list_display` callables execute."""

    @pytest.mark.parametrize(
        "model",
        sorted(admin.site._registry, key=lambda m: (m._meta.app_label, m._meta.model_name)),
        ids=lambda m: f"{m._meta.app_label}.{m._meta.model_name}",
    )
    def test_changelist_change_and_delete_render(self, admin_client_, rich, model):
        prefix = f"admin:{model._meta.app_label}_{model._meta.model_name}"
        assert admin_client_.get(reverse(f"{prefix}_changelist")).status_code == 200

        obj = model.objects.first()
        if obj is None:
            return
        for suffix in ("change", "delete", "history"):
            resp = admin_client_.get(reverse(f"{prefix}_{suffix}", args=[obj.pk]))
            # 403 is legitimate for the read-only trail and the singleton config.
            assert resp.status_code in (200, 403), f"{prefix}_{suffix} -> {resp.status_code}"

    def test_a_payment_without_a_parent_renders_in_the_changelist(self, admin_client_, rich):
        """`parent_link` dereferences `obj.parent`; an adult student has none."""
        html = admin_client_.get(reverse("admin:billing_payment_changelist")).content.decode()
        assert html.count("<tr") > 1

    def test_hostile_free_text_is_escaped_not_executed(self, admin_client_, rich):
        html = admin_client_.get(reverse("admin:students_student_changelist")).content.decode()
        assert HOSTILE not in html, "raw payload must not reach the page unescaped"
        assert "&quot;http://evil/&quot;" in html or "&#x27;" in html or "evil" in html


class TestTeacherSecondFactorIsNotRendered:
    """Teacher was registered bare, so every field became an editable input —
    including the plaintext TOTP seed. Any admin could read a colleague's,
    enrol it in their own authenticator and keep a second factor for that
    account; one borrowed session harvested the lot."""

    @pytest.fixture
    def enrolled(self, teacher):
        teacher.two_factor_secret = "JBSWY3DPEHPK3PXPSECRET"
        teacher.two_factor_enabled = True
        teacher.two_factor_backup_codes = ["pbkdf2_sha256$1$salt$hashvalue"]
        teacher.save()
        return teacher

    def test_the_totp_secret_is_absent_from_the_change_form(self, admin_client_, enrolled):
        html = admin_client_.get(reverse("admin:students_teacher_change", args=[enrolled.pk])).content.decode()
        assert "JBSWY3DPEHPK3PXPSECRET" not in html

    def test_the_backup_code_hashes_are_absent(self, admin_client_, enrolled):
        html = admin_client_.get(reverse("admin:students_teacher_change", args=[enrolled.pk])).content.decode()
        assert "pbkdf2_sha256$1$salt$hashvalue" not in html

    def test_the_credential_fields_are_excluded_from_the_form(self):
        excluded = set(admin.site._registry[Teacher].exclude or ())
        assert {"two_factor_secret", "two_factor_backup_codes"} <= excluded

    def test_enrolment_state_is_still_visible_but_read_only(self, admin_client_, enrolled):
        ma = admin.site._registry[Teacher]
        assert "two_factor_enabled" in ma.readonly_fields
        html = admin_client_.get(reverse("admin:students_teacher_change", args=[enrolled.pk])).content.decode()
        assert 'name="two_factor_enabled"' not in html, "read-only means no input to toggle"


class TestTeacherCreatedInAdminCanLogIn:
    """Authentication goes through the linked `auth.User`, and
    `/password-reset/` matches on it too. Adding a Teacher here left `user`
    NULL, so the account could neither sign in nor be activated — the same trap
    `create_teacher` in the app UI already avoids."""

    def _add(self, client, email="nueva@example.com"):
        return client.post(
            reverse("admin:students_teacher_add"),
            {"first_name": "Nueva", "last_name": "Profe", "email": email, "phone": "600000000", "active": "on"},
        )

    def test_an_auth_user_is_created(self, admin_client_):
        assert self._add(admin_client_).status_code == 302

        teacher = Teacher.objects.get(email="nueva@example.com")
        assert teacher.user is not None
        assert teacher.user.username == "nueva@example.com"

    def test_the_account_awaits_activation_rather_than_holding_a_password(self, admin_client_):
        self._add(admin_client_)
        assert Teacher.objects.get(email="nueva@example.com").user.has_usable_password() is False

    def test_teachers_added_here_are_not_silently_staff(self, admin_client_):
        self._add(admin_client_)
        user = Teacher.objects.get(email="nueva@example.com").user
        assert user.is_superuser is False
        assert user.is_staff is False


class TestScheduleSlotAdminRespectsTheGrid:
    """`save_schedule_slot` validates against the real grid with
    `is_valid_slot`; the admin had no such check and could write a cell the
    rest of the app considers impossible."""

    def _add(self, client, group, row, day, col):
        return client.post(
            reverse("admin:core_scheduleslot_add"),
            {"row": str(row), "day": str(day), "col": str(col), "group": str(group.pk)},
        )

    def test_friday_has_no_third_row(self, admin_client_, group):
        """Friday runs four overlapping sessions keyed on (row, col); row 2
        does not exist there."""
        assert self._add(admin_client_, group, 2, 4, 0).status_code == 200  # form redisplayed
        assert not ScheduleSlot.objects.filter(row=2, day=4, col=0).exists()

    def test_a_slot_far_outside_the_grid_is_refused(self, admin_client_, group):
        assert self._add(admin_client_, group, 99, 99, 99).status_code == 200
        assert not ScheduleSlot.objects.filter(row=99).exists()

    def test_a_real_cell_still_saves(self, admin_client_, group):
        assert self._add(admin_client_, group, 1, 2, 0).status_code == 302
        assert ScheduleSlot.objects.filter(row=1, day=2, col=0).exists()

    def test_an_existing_bad_row_is_flagged_rather_than_hidden(self, admin_client_, group):
        """Rows written before the validator existed must still be findable."""
        ScheduleSlot.objects.create(row=2, day=4, col=0, group=group)
        html = admin_client_.get(reverse("admin:core_scheduleslot_changelist")).content.decode()
        assert "fuera del cuadrante" in html


class TestHistoryLogIsNotHandWritable:
    """Every field was read-only but Add was still offered, so the add form
    rendered with no inputs and saving it created `action=""`, `message=""`.
    The feed is capped at 1,000 rows, so each blank row evicted a real one."""

    def test_the_add_form_is_refused(self, admin_client_):
        assert admin_client_.get(reverse("admin:core_historylog_add")).status_code == 403

    def test_posting_an_empty_form_creates_nothing(self, admin_client_):
        before = HistoryLog.objects.count()
        assert admin_client_.post(reverse("admin:core_historylog_add"), {}).status_code == 403
        assert HistoryLog.objects.count() == before

    def test_existing_entries_are_still_readable(self, admin_client_, rich):
        assert admin_client_.get(reverse("admin:core_historylog_changelist")).status_code == 200


class TestAuditTrailCannotBeErased:
    """Add and change were blocked and the docstring claimed immutability, but
    delete was never overridden — so the record of who changed what could be
    removed by the account it incriminates."""

    @pytest.fixture
    def entry(self):
        return AuditLog.objects.create(
            action="update", model="Student", object_id="1", object_label="Leo", actor_label="ada@example.com"
        )

    def test_single_delete_is_forbidden(self, admin_client_, entry):
        assert (
            admin_client_.post(reverse("admin:core_auditlog_delete", args=[entry.pk]), {"post": "yes"}).status_code
            == 403
        )
        assert AuditLog.objects.filter(pk=entry.pk).exists()

    def test_bulk_delete_does_nothing(self, admin_client_, entry):
        admin_client_.post(
            reverse("admin:core_auditlog_changelist"),
            {"action": "delete_selected", "_selected_action": [str(entry.pk)], "index": "0", "post": "yes"},
        )
        assert AuditLog.objects.filter(pk=entry.pk).exists()

    def test_add_and_change_stay_blocked(self, admin_client_, entry):
        assert admin_client_.get(reverse("admin:core_auditlog_add")).status_code == 403
        assert admin_client_.get(reverse("admin:core_auditlog_change", args=[entry.pk])).status_code in (200, 403)


class TestEnrollmentTypeIsProtectedReferenceData:
    """`_resolve_enrollment_type` looks these up BY NAME and raises when one is
    missing — which blocks every enrollment of every kind, because
    `Enrollment.enrollment_type` is a non-null PROTECT FK. An unreferenced row
    had nothing protecting it."""

    def test_the_four_required_rows_cannot_be_deleted(self, admin_client_, enrollment_type_special):
        url = reverse("admin:billing_enrollmenttype_delete", args=[enrollment_type_special.pk])
        assert admin_client_.post(url, {"post": "yes"}).status_code == 403
        assert EnrollmentType.objects.filter(pk=enrollment_type_special.pk).exists()

    def test_the_lookup_key_is_read_only_once_the_row_exists(self, admin_client_, enrollment_type_special):
        html = admin_client_.get(
            reverse("admin:billing_enrollmenttype_change", args=[enrollment_type_special.pk])
        ).content.decode()
        assert 'name="name"' not in html

    def test_the_matricula_amounts_are_visible_in_the_list(self, admin_client_, enrollment_type_special):
        ma = admin.site._registry[EnrollmentType]
        assert "base_amount_full_time" in ma.list_display


class TestFieldsTheAcademyNeedsAreReachable:
    """Each of these was absent from every fieldset, so there was nowhere in
    the entire app — admin included — to see or correct it."""

    def _form_html(self, client, url_name, pk):
        return client.get(reverse(url_name, args=[pk])).content.decode()

    @pytest.mark.parametrize("field", ["is_adult", "email", "phone", "waiting_contact_name", "waiting_contact_phone"])
    def test_student_contact_and_status_fields(self, admin_client_, student, field):
        html = self._form_html(admin_client_, "admin:students_student_change", student.pk)
        assert f'name="{field}"' in html

    def test_parent_sms_consent(self, admin_client_, parent):
        """`comms.tasks` gates every SMS on this flag."""
        html = self._form_html(admin_client_, "admin:students_parent_change", parent.pk)
        assert 'name="sms_opt_in"' in html

    @pytest.mark.parametrize(
        "field", ["academic_year", "payment_modality", "is_sibling_discount", "has_language_cheque"]
    )
    def test_enrollment_billing_plan_fields(self, admin_client_, active_enrollment, field):
        """`academic_year` is what `generate_payments` filters on — an
        enrollment stamped with the wrong year is simply never billed."""
        html = self._form_html(admin_client_, "admin:billing_enrollment_change", active_enrollment.pk)
        assert f'name="{field}"' in html


class TestChangelistQueryCost:
    def test_group_changelist_does_not_scale_with_rows(self, admin_client_, rich, django_assert_max_num_queries):
        """`enrolled_count` and `available_spots` each counted the group's
        students with their own query, on top of the `teacher` FK — three round
        trips per row (39 for 13 groups)."""
        teacher = rich["teacher"]
        for i in range(12):
            Group.objects.create(group_name=f"G{i}", teacher=teacher, max_students=8, active=True)

        with django_assert_max_num_queries(15):
            assert admin_client_.get(reverse("admin:students_group_changelist")).status_code == 200

    def test_uncapped_groups_report_no_limit_rather_than_full(self, admin_client_, rich):
        """`max_students=0` means "no cap", not "zero places"."""
        html = admin_client_.get(reverse("admin:students_group_changelist")).content.decode()
        assert "sin límite" in html


class TestPaymentBulkActions:
    """`mark_as_completed` was a bare `queryset.update(payment_date=today)`.

    That had the two bugs the UI path already guards against: it rewrote
    `payment_date` on rows that were ALREADY completed — moving settled money
    into the current month in every income report, the same regression
    `quick_complete_payment` short-circuits on — and it sent no receipt, so a
    cash or transfer payment completed from the admin was silently
    unacknowledged.
    """

    def _admin(self):
        return PaymentAdmin(Payment, AdminSite())

    def _request(self, rf):
        request = rf.post("/admin/")
        request._messages = _SilentMessages()
        return request

    def test_an_already_completed_payment_keeps_its_original_date(self, rf, completed_payment):
        original = completed_payment.payment_date

        with patch("core.views.payments._queue_payment_receipt") as receipt:
            self._admin().mark_as_completed(self._request(rf), Payment.objects.filter(id=completed_payment.id))

        completed_payment.refresh_from_db()
        assert completed_payment.payment_date == original
        receipt.assert_not_called()

    def test_a_pending_payment_is_completed_and_gets_one_receipt(self, rf, pending_payment):
        with patch("core.views.payments._queue_payment_receipt") as receipt:
            self._admin().mark_as_completed(self._request(rf), Payment.objects.filter(id=pending_payment.id))

        pending_payment.refresh_from_db()
        assert pending_payment.payment_status == "completed"
        assert pending_payment.payment_date == date.today()
        receipt.assert_called_once_with(pending_payment.id)

    @pytest.mark.parametrize("action", ["mark_as_pending", "restore_payments"])
    def test_reopening_clears_the_collection_date(self, rf, completed_payment, action):
        """A pending payment has not been collected, and every income figure
        filters on `payment_date` — leaving it set reported money nobody paid."""
        getattr(self._admin(), action)(self._request(rf), Payment.objects.filter(id=completed_payment.id))

        completed_payment.refresh_from_db()
        assert completed_payment.payment_status == "pending"
        assert completed_payment.payment_date is None


class _SilentMessages:
    """Minimal messages backend so `message_user` works on a bare RequestFactory."""

    def add(self, *args, **kwargs):
        return None
