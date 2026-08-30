# students — People Management

The `students` app owns all people-related models: students, parents, teachers, and groups. It is the foundation that billing and comms depend on.

## Models

| Model | Table | Key Fields | Relationships |
| ----- | ----- | ---------- | ------------- |
| **Teacher** | `teachers` | first_name, last_name, email (unique), phone, active, admin, user (OneToOne → `auth.User`), two_factor_secret / _enabled / _backup_codes (v1.13) | Has many Groups; linked to a Django auth user for login |
| **Group** | `groups` | group_name (unique), color (hex), max_students (v1.1 capacity), active | FK to Teacher; has many Students |
| **Parent** | `parents` | first_name, last_name, dni (unique), phone, email, iban, sms_opt_in (v1.8) | M2M to Students via StudentParent |
| **Student** | `students` | first_name, last_name (blank, v1.17.5), birth_date (nullable, v1.15), gender (m/f), is_adult, school, allergies, gdpr_signed, active, is_waiting / waiting_since (v1.1) / waiting_priority (v1.17.5), withdrawal_date / withdrawal_reason, course / observations / waiting_contact_name / waiting_contact_phone (v1.15) | FK to Group (nullable, v1.15); M2M to Parents |
| **StudentParent** | `student_parents` | student, parent | Through table for Student-Parent M2M |
| **ParentSessionToken** | `parent_session_tokens` | parent, token (hashed), expires_at, used_at — in `parent_portal_models.py` (v1.9) | FK to Parent; single-use magic-link token for the parent portal |

### Key Properties

- `Student.full_name` — "{first_name} {last_name}", stripped: `last_name` is blank for a waiting-list entry taken over the phone
- `Student.age` — calculated from birth_date; returns `None` when no birth date is on record (waiting-list entries may only have a name and a phone number)
- `Student.gender` — 'm' or 'f' (used in enrollment confirmation emails)
- `Parent.full_name` — "{first_name} {last_name}"
- `Teacher.full_name` — "{first_name} {last_name}"
- `Group.max_students` / capacity properties — occupancy and free seats, used by the waiting list (v1.1)
- `Student.is_waiting` / `waiting_since` — waiting students are excluded from the main student list (v1.1)
- `Student.waiting_priority` (v1.17.5) — jumps the FIFO queue. It is part of the waiting list's `ORDER BY` (`-waiting_priority, waiting_since, created_at`), not just a badge. Set on the create form, edited afterwards from `/admin/`
- `ParentSessionToken.consume()` — single-use redemption under `SELECT FOR UPDATE`, so a magic link can't be redeemed twice concurrently (v1.9)

### Teacher → auth.User link

Each Teacher can be linked to a Django `auth.User` via a nullable `OneToOneField` (`Teacher.user`, related_name `teacher`). This is what makes Teacher email + password login work in testing and production:

- **`Teacher.ensure_user(password=None)`** — idempotent helper that get-or-creates the linked User (username = email), syncs first_name / last_name / email, mirrors `Teacher.admin` onto `is_staff` + `is_superuser`, and optionally sets a hashed password. Omitting the password leaves the user with `unusable_password` so they must use `/password-reset/` to activate the account.
- **`post_save` signal** — when an existing Teacher is updated, the signal mirrors email/name/admin flags onto the linked User so the two records never drift.
- **Migration** — `0003_teacher_user` adds the FK as `null=True, on_delete=SET_NULL` so existing Teachers remain valid without a linked User. The app is at **7 migrations**, through `0007_add_teacher_two_factor`.

Dev environment (`DJANGO_ENV=development`) keeps using the legacy env-var basic-auth via `LOGIN_USERNAME` / `LOGIN_PASSWORD` and never touches Teacher login; the linked User is only required in testing/production.

## Forms

- **StudentForm** — ModelForm for Student (first_name, last_name, birth_date, school, allergies, gdpr_signed, group). Validates birth_date is not in the future, and re-asserts `last_name.required = True` — the model field is `blank=True` for the waiting-list form, which would otherwise make the surname optional on the real ficha too.
- **WaitingListForm** (v1.15) — deliberately minimal form for taking a waiting-list entry over the phone. Only the student's first name and a contact phone are required; the **surname is not asked for at all** (v1.17.5 — it is collected when the family is offered a place), and parent name, `course`, `age`, `waiting_priority` and `observations` are optional with the preferred group blank-able. The contact is stored on the Student (`waiting_contact_*`) rather than as a `Parent`, because `Parent` requires a unique DNI that nobody has to hand during a call. An `age` is converted to an approximate `birth_date` so the age column has something to show.
- **ParentForm** — (v1.15: DNI uniqueness is deliberately NOT enforced at form level, so `ParentCreateView` can reuse an existing parent when a second sibling is enrolled instead of showing a raw uniqueness error)  ModelForm for Parent (first_name, last_name, dni, phone, email, iban). Validates DNI minimum 8 characters.
- **ParentFormSet** — Inline formset for StudentParent through model.

## Admin

- `StudentAdmin` with `StudentParentInline` — fieldsets for personal, school, health, status info
- `ParentAdmin` with `ParentStudentInline` — fieldsets for personal and contact info
- `StudentParentAdmin` with autocomplete
- `Teacher` and `Group` — simple registration

## URL Patterns (students/urls.py)

| URL | View | Name |
| --- | ---- | ---- |
| `parents/create/` | ParentCreateView | `parent_create` |
| `students/` | StudentListView | `students_list` |
| `students/create/` | StudentCreateView | `student_create` |
| `students/waiting/` | waiting_list_view | `waiting_list` |
| `students/waiting/<id>/assign/` | assign_from_waiting_list | `assign_from_waiting_list` |
| `students/<id>/waiting/add/` | add_to_waiting_list | `add_to_waiting_list` |
| `students/<id>/` | StudentDetailView | `student_detail` |
| `students/<id>/update/` | StudentUpdateView | `student_update` |
| `api/students/<id>/fun-friday/toggle/` | toggle_fun_friday_this_week | `toggle_fun_friday_this_week` |
| `api/students/<id>/fun-friday/add/` | add_fun_friday_attendance | `add_fun_friday_attendance` |
| `api/students/<id>/fun-friday/remove/` | remove_fun_friday_attendance | `remove_fun_friday_attendance` |
| `api/search/students/` | search_students | `search_students` |
| `api/search/parents/` | search_parents | `search_parents` |
| `api/validate/student-parent/` | validate_student_parent | `validate_student_parent` |

## Cross-App Communication

- **Depended on by**: billing (FK from Enrollment/Payment to Student/Parent), comms (email recipients), core (schedule slots, fun friday)
- **Depends on**: nothing — students is the foundational app
- **Note**: Views currently live in `core/views/students.py` and `core/views/parents.py`. URL routing happens here, view code is imported from core.
