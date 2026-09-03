"""Fixes around the students app's periphery — forms, admin, audit label.

Five separate bugs, all in code the rest of the suite reaches only indirectly:

* `StudentForm.clean_group` read `is_waiting` out of `cleaned_data`, but Django
  cleans fields in `Meta.fields` order and `group` comes first — so the
  waiting-list exemption was dead code and editing a waiting-list student whose
  preferred group is full (the normal state: that is WHY they are on the list)
  was refused.
* `StudentAdmin` set no `form`, so the group cap did not exist in `/admin/` at
  all. `*/admin.py` is excluded from coverage, which is how rules rot there.
* The admin's "Reenviar invitación" action CLEARED `portal_invite_sent_at`
  instead of stamping it, permanently disarming the once-only invite guard.
* `ParentAdmin` left `email` freely editable with no duplicate check, and two
  Parent rows sharing an address lock BOTH families out of the portal.
* `AuditLog.object_label` was `str(instance)`, and `Parent.__str__` embeds the
  DNI — the one field the audit allow-list exists to exclude.
"""

from datetime import date

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.db.models import Count, Q
from django.urls import reverse

from core.audit_models import AuditLog
from students.forms import ParentForm, StudentForm
from students.models import Group, Parent, Student

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fill(group, n=None):
    """Occupy `n` (default: all) places in `group` with enrolled students."""
    n = group.max_students if n is None else n
    return [
        Student.objects.create(first_name=f"Lleno{i}", last_name="Kid", group=group, active=True, is_waiting=False)
        for i in range(n)
    ]


def _waiting(group, **extra):
    return Student.objects.create(
        first_name="Espera",
        last_name="Kid",
        group=group,
        active=True,
        is_waiting=True,
        **extra,
    )


def _student_form_data(group, **extra):
    data = {
        "first_name": "Nuevo",
        "last_name": "Alumno",
        "birth_date": "2015-01-01",
        "school": "CEIP Test",
        "group": str(group.pk),
    }
    data.update(extra)
    return data


@pytest.fixture
def admin_ui(client):
    """Client authenticated for BOTH `django.contrib.admin` and SimpleAuthMiddleware."""
    user = User.objects.create_superuser(username="periphery-admin", email="pa@example.com", password="x")
    client.force_login(user)
    session = client.session
    session["is_authenticated"] = True
    session["username"] = user.username
    session.save()
    return client


def _admin_student_payload(group, **extra):
    """A complete POST for `/admin/students/student/add/`, inline forms included."""
    data = {
        "first_name": "Admin",
        "last_name": "Alumno",
        "gender": "m",
        "birth_date": "",
        "school": "",
        "course": "",
        "email": "",
        "phone": "",
        "allergies": "",
        "observations": "",
        "group": str(group.pk),
        "waiting_contact_name": "",
        "waiting_contact_phone": "",
        "withdrawal_date": "",
        "withdrawal_reason": "",
        "active": "on",
        "studentparent_set-TOTAL_FORMS": "0",
        "studentparent_set-INITIAL_FORMS": "0",
        "studentparent_set-MIN_NUM_FORMS": "0",
        "studentparent_set-MAX_NUM_FORMS": "1000",
    }
    data.update(extra)
    return data


def _admin_parent_payload(**extra):
    data = {
        "first_name": "Admin",
        "last_name": "Tutor",
        "dni": "99999999Z",
        "phone": "600000000",
        "email": "admin.tutor@example.com",
        "iban": "",
        "studentparent_set-TOTAL_FORMS": "0",
        "studentparent_set-INITIAL_FORMS": "0",
        "studentparent_set-MIN_NUM_FORMS": "0",
        "studentparent_set-MAX_NUM_FORMS": "1000",
    }
    data.update(extra)
    return data


# ---------------------------------------------------------------------------
# 1 — the waiting-list exemption is live again
# ---------------------------------------------------------------------------


class TestGroupCapExemptsWaitingListSaves:
    """`clean_group` could never see `is_waiting`: `Meta.fields` puts `group`
    at index 6 and `is_waiting` at index 7, and Django cleans in that order, so
    `.get("is_waiting")` was always None. The check now lives in `clean()`."""

    def test_editing_a_waiting_student_in_a_full_group_is_allowed(self, group):
        group.max_students = 2
        group.save()
        _fill(group)
        waiter = _waiting(group)

        form = StudentForm(
            data=_student_form_data(group, first_name="Corregido", is_waiting="on"),
            instance=waiter,
        )

        assert form.is_valid(), form.errors
        assert form.save().first_name == "Corregido"

    def test_the_cap_still_blocks_a_real_enrollment(self, group):
        group.max_students = 2
        group.save()
        _fill(group)

        form = StudentForm(data=_student_form_data(group))

        assert not form.is_valid()
        assert "group" in form.errors
        assert "completo" in form.errors["group"][0]

    def test_promoting_a_waiter_into_a_full_group_is_still_refused(self, group):
        """Unticking the box means they take a place, so the cap applies."""
        group.max_students = 2
        group.save()
        _fill(group)
        waiter = _waiting(group)

        form = StudentForm(data=_student_form_data(group), instance=waiter)

        assert not form.is_valid()
        assert "group" in form.errors

    def test_an_occupant_does_not_block_their_own_edit(self, group):
        """The student being edited is one of the occupants counted."""
        group.max_students = 1
        group.save()
        occupant = _fill(group)[0]

        form = StudentForm(data=_student_form_data(group, first_name="Renombrado"), instance=occupant)

        assert form.is_valid(), form.errors

    def test_an_uncapped_group_never_blocks(self, group):
        """`max_students == 0` means no cap, not no room."""
        group.max_students = 0
        group.save()
        _fill(group, 12)

        assert StudentForm(data=_student_form_data(group)).is_valid()

    def test_the_waiting_kwarg_exempts_a_caller_whose_flag_is_not_in_the_post(self, group):
        """`StudentCreateView?mode=waiting` reads the mode from the QUERY STRING
        and the create template never renders the checkbox, so the form cannot
        see it — the view has to declare it."""
        group.max_students = 1
        group.save()
        _fill(group)

        assert StudentForm(data=_student_form_data(group), waiting=True).is_valid()
        assert not StudentForm(data=_student_form_data(group)).is_valid()


# ---------------------------------------------------------------------------
# 2 — the cap now exists in the admin
# ---------------------------------------------------------------------------


class TestAdminHonoursTheGroupCap:
    """`StudentAdmin` had no `form`, so an admin edit over-filled a group past
    `max_students` — and a 9th student in an 8-place group then made
    `Group.is_full` refuse every waiting-list promotion into it."""

    def _add(self, client, payload):
        return client.post(reverse("admin:students_student_add"), payload)

    def test_a_full_group_is_refused(self, admin_ui, group):
        group.max_students = 1
        group.save()
        _fill(group)

        response = self._add(admin_ui, _admin_student_payload(group))

        assert response.status_code == 200  # form redisplayed
        assert not Student.objects.filter(first_name="Admin").exists()

    def test_the_refusal_names_the_group(self, admin_ui, group):
        group.max_students = 1
        group.save()
        _fill(group)

        html = self._add(admin_ui, _admin_student_payload(group)).content.decode()

        assert "completo" in html
        assert group.group_name in html

    def test_a_waiting_list_entry_is_exempt(self, admin_ui, group):
        """`is_waiting` is in the Status fieldset, so the exemption reads
        straight off `cleaned_data` here."""
        group.max_students = 1
        group.save()
        _fill(group)

        response = self._add(admin_ui, _admin_student_payload(group, is_waiting="on"))

        assert response.status_code == 302
        assert Student.objects.get(first_name="Admin").is_waiting is True

    def test_a_group_with_room_still_saves(self, admin_ui, group):
        response = self._add(admin_ui, _admin_student_payload(group))

        assert response.status_code == 302
        assert Student.objects.filter(first_name="Admin", group=group).exists()


# ---------------------------------------------------------------------------
# 3 — "Reenviar invitación" must not disarm the once-only guard
# ---------------------------------------------------------------------------


class TestResendPortalInvitationStampsTheGuard:
    """The action calls the DIRECT sender, so the guard was never in its way —
    clearing it achieved nothing for the resend and left it open forever. The
    family's next sibling enrolment then fired a duplicate invitation AND
    rotated the temporary password they were still holding."""

    def _resend(self, client, parent):
        return client.post(
            reverse("admin:students_parent_changelist"),
            {"action": "resend_portal_invitation", "_selected_action": [str(parent.pk)], "index": "0"},
            follow=True,
        )

    def test_the_guard_is_stamped_not_cleared(self, admin_ui, parent):
        assert parent.portal_invite_sent_at is None

        assert self._resend(admin_ui, parent).status_code == 200

        parent.refresh_from_db()
        assert parent.portal_invite_sent_at is not None

    def test_a_later_sibling_enrolment_does_not_re_invite(self, admin_ui, parent, rf):
        """`send_portal_invitation_once` must still refuse after a resend."""
        from core.views.parent_portal import send_portal_invitation_once

        self._resend(admin_ui, parent)
        parent.refresh_from_db()

        assert send_portal_invitation_once(rf.get("/"), parent) is False

    def test_a_temporary_password_is_still_issued(self, admin_ui, parent):
        self._resend(admin_ui, parent)

        parent.refresh_from_db()
        assert parent.temporary_password
        assert parent.temporary_password_issued_at is not None

    def test_a_parent_without_an_email_is_skipped(self, admin_ui):
        mute = Parent.objects.create(first_name="Sin", last_name="Correo", dni="55555555K", phone="600555000", email="")

        self._resend(admin_ui, mute)

        mute.refresh_from_db()
        assert mute.portal_invite_sent_at is None


# ---------------------------------------------------------------------------
# 4 — duplicate portal email
# ---------------------------------------------------------------------------


class TestDuplicatePortalEmail:
    """`Parent.email` is not unique and `_parent_by_email` refuses an ambiguous
    match, so two rows sharing an address lock BOTH families out of login and
    out of password recovery."""

    def _add(self, client, payload):
        return client.post(reverse("admin:students_parent_add"), payload)

    def test_the_admin_refuses_a_new_collision(self, admin_ui, parent):
        response = self._add(admin_ui, _admin_parent_payload(email=parent.email))

        assert response.status_code == 200
        assert not Parent.objects.filter(dni="99999999Z").exists()

    def test_the_refusal_explains_the_lockout(self, admin_ui, parent):
        html = self._add(admin_ui, _admin_parent_payload(email=parent.email)).content.decode()

        assert "portal de familias" in html

    def test_a_different_email_saves_normally(self, admin_ui, parent):
        response = self._add(admin_ui, _admin_parent_payload(email="otro.tutor@example.com"))

        assert response.status_code == 302
        assert Parent.objects.filter(email="otro.tutor@example.com").exists()

    def test_the_case_of_the_address_does_not_hide_the_collision(self, admin_ui, parent):
        """The portal resolves the family with `iexact`, so `Ana@x` and `ana@x`
        are the same lockout."""
        response = self._add(admin_ui, _admin_parent_payload(email=parent.email.upper()))

        assert response.status_code == 200

    def test_a_preexisting_collision_stays_editable(self, admin_ui, parent):
        """Refusing here would make BOTH colliding rows unsaveable, so neither
        could ever be corrected — including by the admin trying to fix this."""
        twin = Parent.objects.create(
            first_name="Gemela", last_name="Duplicada", dni="44444444J", phone="600444000", email=parent.email
        )

        response = admin_ui.post(
            reverse("admin:students_parent_change", args=[twin.pk]),
            _admin_parent_payload(
                first_name="Gemela",
                last_name="Duplicada",
                dni="44444444J",
                phone="600999999",
                email=twin.email,
                **{"studentparent_set-INITIAL_FORMS": "0"},
            ),
        )

        assert response.status_code == 302
        twin.refresh_from_db()
        assert twin.phone == "600999999"

    def test_the_app_form_warns_without_blocking(self, parent):
        """The create screen is a phone call in progress, and its form must stay
        VALID so `form_valid` can reuse an existing parent for a second sibling."""
        form = ParentForm(
            data={
                "first_name": "Otra",
                "last_name": "Tutora",
                "dni": "33333333H",
                "phone": "600333000",
                "email": parent.email,
                "iban": "",
            }
        )

        assert form.is_valid(), form.errors
        assert form.email_collides_with_other_family is True

    def test_the_app_form_flag_is_false_for_a_fresh_address(self, parent):
        form = ParentForm(
            data={
                "first_name": "Otra",
                "last_name": "Tutora",
                "dni": "33333333H",
                "phone": "600333000",
                "email": "fresca@example.com",
                "iban": "",
            }
        )

        assert form.is_valid(), form.errors
        assert form.email_collides_with_other_family is False

    def test_the_create_view_surfaces_the_warning(self, authenticated_client, parent):
        from django.contrib.messages import get_messages

        response = authenticated_client.post(
            reverse("parent_create"),
            {
                "first_name": "Otra",
                "last_name": "Tutora",
                "dni": "33333333H",
                "phone": "600333000",
                "email": parent.email,
                "iban": "",
            },
        )

        assert response.status_code == 302
        texts = [str(m).lower() for m in get_messages(response.wsgi_request)]
        assert any("ya existe otro padre/tutor con ese email" in t for t in texts)


# ---------------------------------------------------------------------------
# 5 — the audit trail must not retain the DNI
# ---------------------------------------------------------------------------


class TestAuditLabelIsPiiMinimised:
    """The per-model allow-list deliberately excludes `dni`, `iban`, `phone`
    and `email` from `changes` — and then `object_label = str(instance)` put the
    DNI straight back, into a searchable table kept for two years."""

    def _latest(self, model):
        return AuditLog.objects.filter(model=model).order_by("-id").first()

    def test_a_parent_label_carries_no_dni(self):
        parent = Parent.objects.create(
            first_name="Rosa", last_name="Gil", dni="11223344X", phone="600112233", email="rosa.gil@example.com"
        )

        entry = self._latest("students.Parent")
        assert entry is not None
        assert "11223344X" not in entry.object_label
        assert str(parent.pk) in entry.object_label
        assert "Rosa Gil" in entry.object_label

    def test_an_update_label_carries_no_dni_either(self, parent):
        parent.first_name = "Marta"
        parent.save()

        assert parent.dni not in self._latest("students.Parent").object_label

    def test_a_delete_label_carries_no_dni_either(self):
        doomed = Parent.objects.create(
            first_name="Borra", last_name="Ble", dni="66778899W", phone="600667788", email="borrable@example.com"
        )
        doomed.delete()

        entry = self._latest("students.Parent")
        assert entry.action == "delete"
        assert "66778899W" not in entry.object_label

    def test_the_dni_is_not_in_the_change_blob_either(self, parent):
        """The allow-list side of the same rule — changed alongside a tracked
        field so an `update` row really is written."""
        parent.dni = "77777777Y"
        parent.first_name = "Renombrada"
        parent.save()

        entry = self._latest("students.Parent")
        assert entry.action == "update"
        assert "first_name" in entry.changes
        assert "dni" not in entry.changes

    def test_other_models_keep_their_default_label(self, student):
        student.school = "Otro colegio"
        student.save()

        entry = self._latest("students.Student")
        assert entry.object_label == str(student)[:300]


# ---------------------------------------------------------------------------
# 6 — birth_date is optional, and stays optional
# ---------------------------------------------------------------------------


class TestBirthDateIsOptional:
    """A waiting-list entry is taken over the phone with a first name and a
    number. The field must not advertise itself as required."""

    def test_the_form_field_is_not_required(self):
        assert StudentForm().fields["birth_date"].required is False

    def test_the_widget_does_not_render_the_required_attribute(self):
        assert "required" not in str(StudentForm()["birth_date"])

    def test_a_student_saves_without_one(self, group):
        form = StudentForm(data=_student_form_data(group, birth_date=""))

        assert form.is_valid(), form.errors
        assert form.save().birth_date is None

    def test_age_is_none_rather_than_an_error(self, group):
        kid = Student.objects.create(first_name="Sin", last_name="Fecha", group=group, active=True)

        assert kid.age is None

    def test_a_future_birth_date_is_still_refused(self, group):
        future = date(date.today().year + 2, 1, 1).isoformat()

        assert not StudentForm(data=_student_form_data(group, birth_date=future)).is_valid()


# ---------------------------------------------------------------------------
# 7 — Group counts prefer an annotation
# ---------------------------------------------------------------------------


class TestGroupCountsPreferAnnotations:
    """The four properties are uncached `.count()`s and `available_spots` /
    `is_full` each recompute `enrolled_count`, so one template row touching all
    four cost FOUR queries. `group_capacity_summary()` already resolves them in
    one; the properties now read its annotations."""

    def test_reading_all_four_off_an_annotated_row_is_free(self, group, django_assert_num_queries):
        from core.views.waiting_list import group_capacity_summary

        _fill(group, 1)
        _waiting(group)

        row = next(r for r in group_capacity_summary() if r["id"] == group.pk)
        annotated = row["group"]

        with django_assert_num_queries(0):
            assert annotated.enrolled_count == 1
            assert annotated.waiting_count == 1
            assert annotated.available_spots == group.max_students - 1
            assert annotated.is_full is False

    def test_the_summary_values_are_unchanged(self, group):
        from core.views.waiting_list import group_capacity_summary

        _fill(group, 1)
        _waiting(group)

        row = next(r for r in group_capacity_summary() if r["id"] == group.pk)
        assert row["enrolled"] == 1
        assert row["waiting"] == 1
        assert row["available"] == group.max_students - 1
        assert row["is_full"] is False
        assert row["has_room_for_waiters"] is True

    def test_the_admin_alias_is_honoured_too(self, group):
        """`GroupAdmin.get_queryset` annotates `_enrolled`, not `enrolled`."""
        _fill(group, 2)
        annotated = Group.objects.annotate(
            _enrolled=Count("students", filter=Q(students__active=True, students__is_waiting=False), distinct=True)
        ).get(pk=group.pk)

        assert annotated.enrolled_count == 2

    def test_a_bare_instance_still_counts_for_itself(self, group, django_assert_num_queries):
        _fill(group, 2)
        bare = Group.objects.get(pk=group.pk)

        with django_assert_num_queries(1):
            assert bare.enrolled_count == 2

    def test_has_room_for_discounts_the_student_being_saved(self, group):
        group.max_students = 1
        group.save()
        occupant = _fill(group)[0]

        assert group.has_room_for() is False
        assert Group.objects.get(pk=group.pk).has_room_for(exclude_student_pk=occupant.pk) is True

    def test_an_uncapped_group_always_has_room(self, group):
        group.max_students = 0
        group.save()
        _fill(group, 30)

        assert Group.objects.get(pk=group.pk).has_room_for() is True
        assert Group.objects.get(pk=group.pk).is_full is False


# ---------------------------------------------------------------------------
# 8 — the redundant case-sensitive email index is gone
# ---------------------------------------------------------------------------


class TestParentEmailIndexes:
    """Every reader of `parents.email` uses `email__iexact` (rendered as
    `UPPER(email::text) = …`), `exclude(email="")` or `email__icontains` — none
    of which a plain b-tree on the raw column can serve. It only charged every
    Parent insert and update."""

    def _index_defs(self):
        with connection.cursor() as cur:
            cur.execute("SELECT indexdef FROM pg_indexes WHERE tablename = 'parents'")
            return [row[0] for row in cur.fetchall()]

    def test_the_functional_upper_index_survives(self):
        assert any("upper" in d.lower() for d in self._index_defs())

    def test_no_plain_btree_on_email_remains(self):
        plain = [d for d in self._index_defs() if "(email)" in d and "upper" not in d.lower()]
        assert not plain, f"redundant case-sensitive index on parents.email: {plain}"
