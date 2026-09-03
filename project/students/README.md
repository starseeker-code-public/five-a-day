# students — People Management

The `students` app owns all people-related models: students, parents, teachers, and groups. It is the foundation that billing and comms depend on.

## Models

| Model | Table | Key Fields | Relationships |
| ----- | ----- | ---------- | ------------- |
| **Teacher** | `teachers` | first_name, last_name, email (unique), phone, active, admin, user (OneToOne → `auth.User`), two_factor_secret / _enabled / _backup_codes (v1.13) | Has many Groups; linked to a Django auth user for login |
| **Group** | `groups` | group_name (unique), color (hex), max_students (v1.1 capacity), active | FK to Teacher; has many Students |
| **Parent** | `parents` | first_name, last_name, dni (unique), phone, email (**not** unique), iban, sms_opt_in (v1.8), password + temporary_password + temporary_password_issued_at + portal_invite_sent_at (v1.27) | M2M to Students via StudentParent |
| **Student** | `students` | first_name, last_name (blank, v1.20.0), birth_date (nullable, v1.15), gender (m/f), is_adult, school, allergies, gdpr_signed, active, is_waiting / waiting_since (v1.1) / waiting_priority (v1.20.0), withdrawal_date / withdrawal_reason, course / observations / waiting_contact_name / waiting_contact_phone (v1.15) | FK to Group (nullable, v1.15); M2M to Parents |
| **StudentParent** | `student_parents` | student, parent | Through table for Student-Parent M2M |

### Key Properties

- `Student.full_name` — "{first_name} {last_name}", stripped: `last_name` is blank for a waiting-list entry taken over the phone
- `Student.age` — calculated from birth_date; returns `None` when no birth date is on record (waiting-list entries may only have a name and a phone number)
- `Student.gender` — 'm' or 'f' (used in enrollment confirmation emails)
- `Parent.full_name` — "{first_name} {last_name}"
- `Teacher.full_name` — "{first_name} {last_name}"
- `Group.max_students` / capacity properties — occupancy and free seats, used by the waiting list (v1.1)
- `Group.has_room_for(*, exclude_student_pk=None)` (v1.27.1) — **THE** capacity predicate. `students.forms.GroupCapacityMixin` backs both the app's student forms and the admin's onto this one method, so the cap cannot be enforced two different ways — the rule used to live in `StudentForm.clean_group` alone, which `/admin/` never went through. `max_students == 0` means "no cap", not "no room". `exclude_student_pk` discounts the student currently being saved, which is what lets an **edit** that keeps a student in an already-full group through: they are one of the occupants being counted
- `Group.occupied_count(exclude_student_pk=None)` (v1.27.1) — a fresh `COUNT` of the students occupying a place, ignoring annotations
- `Group.enrolled_count` / `waiting_count` now **prefer an annotation the queryset already resolved** (`ENROLLED_ANNOTATIONS` = `enrolled`, `_enrolled`; `WAITING_ANNOTATIONS` = `waiting`, `_waiting` — the aliases `core.views.waiting_list.group_capacity_summary()` and `GroupAdmin.get_queryset` produce), falling back to a live count on a bare object. Without that, one template row touching `enrolled_count`, `waiting_count`, `available_spots` and `is_full` cost **four** queries, because the last two recompute the first. They read `instance.__dict__` rather than `getattr`, since an annotation lands there while `getattr` would walk the class and could hand back the property itself or a same-named related manager. `is_full` is now literally `not has_room_for()`
- `Parent.audit_label` (v1.27.1) — the PII-minimised label `core.audit_signals` writes to `AuditLog.object_label`. That field defaulted to `str(instance)`, and this model's `__str__` embeds the **DNI** — the one field the audit allow-list goes out of its way to exclude from `changes` — so the DNI of every parent was written into a searchable log retained for two years. The pk plus the name identifies the row just as well
- `Parent.other_families_sharing_email(email, *, exclude_pk=None)` (v1.27.1) — the single predicate behind every write path's duplicate-email check (`ParentForm` / `ParentCreateView` warn, `ParentAdminForm` refuses a *new* collision), so the app UI and the admin cannot disagree about what a collision is. `Parent.email` is not unique and `_parent_by_email` deliberately **refuses** an ambiguous match rather than serving one family another family's payment history, so two rows carrying one address lock **both** of them out of portal login *and* recovery. Matched with `iexact`, because that is how the login resolves it — `Ana@x.com` and `ana@x.com` are the same lockout
- `Student.is_waiting` / `waiting_since` — waiting students are excluded from the main student list (v1.1)
- `Student.waiting_priority` (v1.20.0) — jumps the FIFO queue. It is part of the waiting list's `ORDER BY` (`-waiting_priority, waiting_since, created_at`), not just a badge. Set on the create form, edited afterwards from `/admin/`
- `Parent.set_portal_password()` / `authenticate_portal()` / `has_portal_password` — the family-portal credential, hashed with Django's configured hashers (v1.27). Deliberately **not** an `auth.User`: `core.views.auth._authenticate_teacher` authenticates any `auth.User`, so a family holding one would hold a staff login. `authenticate_portal` runs the hasher against a dummy value before refusing a parent who has no credential at all, so "not onboarded" is not measurably faster than "wrong password"
- `Parent.issue_temporary_password()` / `temporary_password` / `temporary_password_issued_at` / `has_temporary_password` (v1.27) — generates a one-off password, stores it hashed and returns the plaintext exactly once (the caller emails it and drops it). Stored in a **second column, beside `password` and not over it**: "he olvidado mi contraseña" is unauthenticated, so overwriting the real credential would let anyone who knows a family's address lock them out of their own payment history. `authenticate_portal()` accepts either and reports which matched, so the view can force a change after a temporary one; `set_portal_password()` clears it, which is what stops an old recovery email remaining a live key. It deliberately does **not** expire — that forced change is what substitutes for a TTL
- `Parent.portal_invite_sent_at` — once-only guard for the portal invitation, so a family with three children receives exactly one

### Teacher → auth.User link

Each Teacher can be linked to a Django `auth.User` via a nullable `OneToOneField` (`Teacher.user`, related_name `teacher`). This is what makes Teacher email + password login work in testing and production:

- **`Teacher.ensure_user(password=None)`** — idempotent helper that get-or-creates the linked User (username = email), syncs first_name / last_name / email, mirrors `Teacher.admin` onto `is_staff` + `is_superuser`, and optionally sets a hashed password. Omitting the password leaves the user with `unusable_password` so they must use `/password-reset/` to activate the account.
- **`post_save` signal** — when an existing Teacher is updated, the signal mirrors email/name/admin flags onto the linked User so the two records never drift.
- **`pre_save` on Student** — `_capture_active_transition` stashes the DB row so `post_save` can spot an `active` True→False transition and notify the waiting list. It fetches the **full** row and publishes it as `instance._presave_db_obj`, which `core.audit_signals` reuses: both are `pre_save` receivers that wanted the same row and each was issuing its own `objects.get()`, so every Student save cost two round trips for one row. Correctness does not depend on receiver order — the audit receiver falls back to fetching for itself.
- **Migration** — `0003_teacher_user` adds the FK as `null=True, on_delete=SET_NULL` so existing Teachers remain valid without a linked User. The app is at **17 migrations**, through `0017_remove_parent_parents_email_346afe_idx` (v1.27.1 — drops a redundant plain b-tree on `parents.email`; every reader goes through `email__iexact`, which uses the `Upper("email")` functional index instead, so the plain one earned nothing and cost write throughput on every Parent insert).

Dev environment (`DJANGO_ENV=development`) keeps using the legacy env-var basic-auth via `LOGIN_USERNAME` / `LOGIN_PASSWORD` and never touches Teacher login; the linked User is only required in testing/production.

## Forms

- **StudentForm** — ModelForm for Student (first_name, last_name, birth_date, school, allergies, gdpr_signed, group, is_waiting). Validates birth_date is not in the future, and re-asserts `last_name.required = True` — the model field is `blank=True` for the waiting-list form, which would otherwise make the surname optional on the real ficha too. `birth_date` is explicitly `required = False` (v1.27.1): it is genuinely optional, and the template was printing a red required asterisk beside it.
- **GroupCapacityMixin** (v1.27.1) — enforces `Group.max_students` from the form's cross-field `clean()`, exempting waiting-list entries. It lives in `clean()` and **not** in a `clean_group()` hook because Django cleans fields in `Meta.fields` order: `group` is cleaned before `is_waiting`, so the old `clean_group` read `cleaned_data.get("is_waiting")` before that key existed and the exemption was dead code — which meant editing a waiting-list student whose preferred group was full (the normal state, since a full group is *why* they are waiting) was rejected outright, even for a name correction. The predicate itself is `Group.has_room_for()` on the model and the message is a single shared constant, so the app form and the admin form cannot drift. `StudentForm(..., waiting=True)` lets a caller assert the flag when it is not in the submitted data — `StudentCreateView` needs this because it reads the waiting mode from the query string and never renders the checkbox.
- **WaitingListForm** (v1.15) — deliberately minimal form for taking a waiting-list entry over the phone. Only the student's first name and a contact phone are required; the **surname is not asked for at all** (v1.20.0 — it is collected when the family is offered a place), and parent name, `course`, `age`, `waiting_priority` and `observations` are optional with the preferred group blank-able. The contact is stored on the Student (`waiting_contact_*`) rather than as a `Parent`, because `Parent` requires a unique DNI that nobody has to hand during a call. An `age` is converted to an approximate `birth_date` so the age column has something to show.
- **ParentForm** — (v1.15: DNI uniqueness is deliberately NOT enforced at form level, so `ParentCreateView` can reuse an existing parent when a second sibling is enrolled instead of showing a raw uniqueness error)  ModelForm for Parent (first_name, last_name, dni, phone, email, iban). Validates DNI minimum 8 characters. `clean_email` **detects** a portal-email collision and flags it (`email_collides_with_other_family`) rather than raising — raising would invalidate the form and kill that same sibling-reuse path, since a repeat DNI implies a repeat email. `ParentCreateView` turns the flag into a warning. The admin form is stricter; see below.
- **ParentFormSet** — Inline formset for StudentParent through model.

## Admin

`*/admin.py` is excluded from coverage, which is how rules rot there — the five v1.27.1 fixes below
are pinned by `tests/integration/test_students_periphery_fixes.py`, alongside the earlier sweep in
`tests/integration/test_admin_hardening.py`.

- `StudentAdminForm` (v1.27.1) — `StudentAdmin` set no `form`, so the admin's auto-generated
  ModelForm had no capacity check and an admin edit silently over-filled a group past
  `max_students`. A 9th student in an 8-place group then makes `Group.is_full` block
  waiting-list promotions. It now reuses `GroupCapacityMixin`, following the same precedent
  as `ScheduleSlotAdminForm` wiring `is_valid_slot()` into the admin.
- `ParentAdminForm` (v1.27.1) — **hard-refuses** a new or changed email that another family
  already uses. `Parent.email` is not unique and `_parent_by_email` deliberately refuses an
  ambiguous match, so a collision locks **both** families out of portal login *and* recovery,
  surfacing later as an unexplained "email o contraseña incorrectos" support call. A
  *pre-existing* collision with the email left untouched stays saveable — refusing it would
  make both rows permanently uncorrectable — and is surfaced as a warning from `save_model`.
- `ParentAdmin.resend_portal_invitation` (v1.27.1) — now **stamps** `portal_invite_sent_at`
  instead of clearing it. Clearing it disarmed the once-only invitation guard permanently:
  the action calls the direct sender, not the guarded one, so the only effect of the
  `None`-write was that a later sibling enrollment re-fired the invitation *and* rotated the
  temporary password, killing the credential the family had just been told to use.
- `TeacherAdmin` (v1.26.0) — **excludes** `two_factor_secret` and `two_factor_backup_codes`.
  Teacher was registered bare until then, so every field rendered as an editable input,
  including the plaintext TOTP seed: any admin could read a colleague's, enrol it in their
  own authenticator and hold that second factor indefinitely. They are excluded rather than
  made read-only, because a read-only field still prints its value. `two_factor_enabled` is
  visible but read-only — `manage.py reset_two_factor <email>` is the supported recovery
  path. `save_model` calls `ensure_user()`, so a Teacher added here can actually log in and
  be activated via `/password-reset/`; the `login_account` column flags rows that predate
  this and still have no linked `auth.User`.
- `StudentAdmin` with `StudentParentInline` — fieldsets for personal, school, contact,
  health and status info. The contact fieldset (`is_adult`, `email`, `phone`) and the
  waiting-list contact (`waiting_contact_name`, `waiting_contact_phone`) were added in
  v1.26.0: an adult student has no `Parent` row, so those fields are their only contact,
  and a fieldset that omits a field makes it unreachable rather than merely hidden.
- `ParentAdmin` with `ParentStudentInline` — personal and contact info, including
  `sms_opt_in` (v1.26.0). That flag gates every SMS in `comms.tasks` and there was no
  screen anywhere in the app that could grant or revoke it.
- `StudentParentAdmin` with autocomplete
- `GroupAdmin` — annotates the enrolled count in the queryset as `_enrolled`, so the changelist
  costs a fixed number of queries instead of three per row (`max_students=0` reads "sin límite").
  Since v1.27.1 `Group.enrolled_count` reads that alias itself (see `ENROLLED_ANNOTATIONS`), so the
  annotation benefits any code path, not just the columns that were written to look for it

## URL Patterns (students/urls.py)

| URL | View | Name |
| --- | ---- | ---- |
| `parents/create/` | ParentCreateView | `parent_create` |
| `students/` | StudentListView | `students_list` |
| `students/create/` | StudentCreateView | `student_create` |
| `students/waiting/` | waiting_list_view | `waiting_list` |
| `students/waiting/create/` | waiting_list_create | `waiting_list_create` |
| `students/<id>/assign/` | assign_from_waiting_list | `assign_from_waiting_list` |
| `students/<id>/wait/` | add_to_waiting_list | `add_to_waiting_list` |
| `students/<id>/` | StudentDetailView | `student_detail` |
| `students/<id>/update/` | StudentUpdateView | `student_update` |
| `api/students/<id>/enroll/` | enroll_student | `enroll_student` |
| `api/students/<id>/fun-friday/toggle/` | toggle_fun_friday_this_week | `toggle_fun_friday_this_week` |
| `api/students/<id>/fun-friday/add/` | add_fun_friday_attendance | `add_fun_friday_attendance` |
| `api/students/<id>/fun-friday/remove/` | remove_fun_friday_attendance | `remove_fun_friday_attendance` |
| `api/search/students/` | search_students | `search_students` |
| `api/search/parents/` | search_parents | `search_parents` |
| `api/validate/student-parent/` | validate_student_parent | `validate_student_parent` |

**16 URL patterns** total. `student_create`, `student_update`, `parent_create`, `enroll_student` and
`assign_from_waiting_list` are admin-only — out of `NON_ADMIN_ALLOWED_URL_NAMES` since v1.26.8, and
carrying `@admin_required` at the view since v1.27.1 (the two CBVs via
`method_decorator(..., name="dispatch")`).

## Cross-App Communication

- **Depended on by**: billing (FK from Enrollment/Payment to Student/Parent), comms (email recipients), core (schedule slots, fun friday)
- **Depends on**: nothing — students is the foundational app
- **Note**: Views currently live in `core/views/students.py` and `core/views/parents.py`. URL routing happens here, view code is imported from core.
