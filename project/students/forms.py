from django import forms
from django.forms import ModelForm, inlineformset_factory

from students.models import Group, Parent, Student

DATE_INPUT_FORMATS = ["%Y-%m-%d", "%d/%m/%Y"]

#: Shown when `Group.max_students` would be exceeded. Defined once so the app
#: form and the admin form cannot word the same refusal two ways.
GROUP_FULL_MESSAGE = "El grupo «{group}» está completo ({places} plazas). Elige otro grupo o amplía el cupo."

#: Non-blocking notice for a duplicate portal email. Used by
#: `ParentCreateView` (app UI) and by `ParentAdmin.save_model` for a collision
#: that already existed before the save.
PORTAL_EMAIL_COLLISION_WARNING = (
    "Aviso: ya existe otro padre/tutor con ese email. El portal de familias identifica "
    "a cada familia por su email, así que dos cuentas con el mismo email no podrán entrar "
    "ni recuperar la contraseña. Usa un email distinto para cada tutor si ambos van a usar el portal."
)

#: Blocking version, for the admin — where the email is being SET to a value
#: that already belongs to another family. See `ParentAdminForm.clean_email`.
PORTAL_EMAIL_COLLISION_ERROR = (
    "Ya existe otro padre/tutor con este email. El portal de familias identifica a cada "
    "familia por su email y rechaza una coincidencia ambigua, así que guardarlo dejaría a "
    "AMBAS familias sin acceso al portal y sin poder recuperar la contraseña. Usa un email "
    "distinto, o corrige primero el registro duplicado."
)


def validate_group_capacity(group, *, exclude_student_pk=None):
    """Raise when `group` has no room for one more student.

    Thin wrapper over `Group.has_room_for()` — the predicate lives on the model
    so the message can live here and both the app form and the admin form can
    reuse the pair.
    """
    if group is None or group.has_room_for(exclude_student_pk=exclude_student_pk):
        return
    raise forms.ValidationError(GROUP_FULL_MESSAGE.format(group=group.group_name, places=group.max_students))


class GroupCapacityMixin:
    """Enforce `Group.max_students` on every ModelForm that can set `Student.group`.

    The cap was only ever checked on the `assign_from_waiting_list` GET
    redirect, so every real write — creating a student, editing one into a
    different group, the admin — ignored it.

    It lives in `clean()` and NOT in `clean_group()` on purpose. The exemption
    below depends on `is_waiting`, and Django cleans fields in `Meta.fields`
    order: `group` comes before `is_waiting`, so inside `clean_group()` the key
    is not in `cleaned_data` yet, `.get()` returns None and the exemption never
    fired. The visible cost was that editing an existing waiting-list student
    was BLOCKED whenever their preferred group was full — which is the normal
    state of affairs, since students go on the list precisely because the group
    is full — so even a spelling correction to a name could not be saved.
    `exclude(pk=...)` did not save it either: a waiting student was never in the
    occupied queryset to begin with (it filters `is_waiting=False`).

    A waiting-list save is exempt because a waiting entry does not occupy a
    place. Editing a student who is already IN this group is not blocked by the
    group being full either — the current occupant is discounted.
    """

    #: Set by a caller that knows the student will land on the waiting list even
    #: though the checkbox is not part of the submitted data. `None` means "read
    #: it off `cleaned_data`", which is right for every form that renders the
    #: field (the student edit page and the admin both do).
    waiting = None

    def clean(self):
        cleaned = super().clean() or self.cleaned_data

        if self._skips_group_capacity(cleaned):
            return cleaned

        try:
            validate_group_capacity(cleaned.get("group"), exclude_student_pk=self.instance.pk)
        except forms.ValidationError as exc:
            # Attached to `group`, not to the form: the message has to render
            # beside the select the user must change.
            self.add_error("group", exc)
        return cleaned

    def _skips_group_capacity(self, cleaned):
        if self.waiting is not None:
            return bool(self.waiting)
        return bool(cleaned.get("is_waiting"))


class StudentForm(GroupCapacityMixin, ModelForm):
    class Meta:
        model = Student
        fields = [
            "first_name",
            "last_name",
            "birth_date",
            "school",
            "allergies",
            "gdpr_signed",
            "group",
            "is_waiting",
        ]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Nombre"}),
            "last_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Apellidos"}),
            "birth_date": forms.DateInput(format="%Y-%m-%d", attrs={"class": "form-control", "type": "date"}),
            "school": forms.TextInput(attrs={"class": "form-control", "placeholder": "Colegio"}),
            "allergies": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Alergias"}),
            "gdpr_signed": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "group": forms.Select(attrs={"class": "form-control"}),
            "is_waiting": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {
            "first_name": "Nombre",
            "last_name": "Apellidos",
            "birth_date": "Fecha de nacimiento",
            "school": "Colegio",
            "allergies": "Alergias",
            "gdpr_signed": "GDPR Firmado",
            "group": "Grupo",
            "is_waiting": "En lista de espera",
        }

    def __init__(self, *args, waiting=None, **kwargs):
        """`waiting=True` exempts this save from the group-capacity cap.

        For callers whose waiting-list intent is NOT in the submitted data:
        `StudentCreateView` takes it from `?mode=waiting` in the query string
        and the create template never renders the checkbox, so the form has no
        way to see it. Every other caller (the student edit page, the admin)
        renders the field and can leave this as `None`.
        """
        super().__init__(*args, **kwargs)
        if waiting is not None:
            self.waiting = waiting
        birth_date = self.fields["birth_date"]
        birth_date.input_formats = DATE_INPUT_FORMATS
        # `Student.birth_date` is null/blank on the model — a waiting-list entry
        # is taken over the phone with a first name and a number — so the field
        # must not advertise itself as required. Asserted explicitly (rather
        # than left to the ModelForm default) because the create page printed a
        # red asterisk beside it, and `Student.age` returning None for these
        # rows is a supported state, not an accident.
        birth_date.required = False
        birth_date.help_text = "Opcional: puede quedar en blanco si aún no se conoce (un alta por teléfono)."
        # `Student.last_name` is blank=True so a waiting-list entry can be taken
        # over the phone without one. The real ficha still demands it.
        self.fields["last_name"].required = True

    def clean_birth_date(self):
        from datetime import date

        birth_date = self.cleaned_data.get("birth_date")
        if birth_date and birth_date > date.today():
            raise forms.ValidationError("La fecha de nacimiento no puede ser futura")
        return birth_date


class WaitingListForm(ModelForm):
    """Deliberately minimal form for taking a waiting-list entry.

    These are typically filled in during a phone call, so the only required
    fields are a name and a number. Everything the full enrollment needs
    (grupo, colegio, GDPR, fecha de nacimiento, plan de pago) is asked for
    later, when the student is actually promoted off the list — the full
    StudentForm handles that.

    The contact is stored on the Student itself rather than as a Parent,
    because Parent requires a unique DNI we cannot ask for over the phone.
    """

    waiting_contact_name = forms.CharField(
        required=False,
        label="Nombre del padre/madre",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Nombre y apellidos"}),
    )
    waiting_contact_phone = forms.CharField(
        required=True,
        label="Móvil de contacto",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "600 000 000"}),
    )
    # Age is easier to get over the phone than a full date of birth. Stored as
    # an approximate birth_date only if given; otherwise left blank.
    age = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=120,
        label="Edad",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "8"}),
    )

    class Meta:
        model = Student
        # No `last_name`: the surname is asked for when the family is offered a
        # place and the real ficha is filled in, not during the first call.
        fields = ["first_name", "course", "group", "waiting_priority", "observations"]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Nombre del alumno"}),
            "course": forms.TextInput(attrs={"class": "form-control", "placeholder": "3º Primaria"}),
            "group": forms.Select(attrs={"class": "form-control"}),
            "waiting_priority": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "observations": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Preferiría martes y jueves por la tarde…",
                }
            ),
        }
        labels = {
            "first_name": "Nombre del alumno",
            "course": "Curso",
            "group": "Grupo preferido (opcional)",
            "waiting_priority": "Prioritario",
            "observations": "Observaciones",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["group"].required = False
        self.fields["group"].empty_label = "Sin preferencia"
        self.fields["group"].queryset = Group.objects.filter(active=True).order_by("group_name")
        self.fields["course"].required = False
        self.fields["observations"].required = False
        self.fields["waiting_priority"].required = False

    def save(self, commit=True):
        student = super().save(commit=False)
        student.is_waiting = True
        student.active = True
        student.waiting_contact_name = self.cleaned_data.get("waiting_contact_name", "")
        student.waiting_contact_phone = self.cleaned_data.get("waiting_contact_phone", "")

        # Derive an approximate birth date from the age when one was given, so
        # the age column has something to show. Only the year is meaningful.
        age = self.cleaned_data.get("age")
        if age:
            from datetime import date

            student.birth_date = date(date.today().year - age, 1, 1)

        if commit:
            student.save()
        return student


class ParentForm(forms.ModelForm):
    class Meta:
        model = Parent
        fields = ["first_name", "last_name", "dni", "phone", "email", "iban"]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Nombre"}),
            "last_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Apellidos"}),
            "dni": forms.TextInput(attrs={"class": "form-control", "placeholder": "12345678A"}),
            "phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "Teléfono"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "email@ejemplo.com"}),
            "iban": forms.TextInput(attrs={"class": "form-control", "placeholder": "ES00 0000 0000 00 0000000000"}),
        }
        labels = {
            "first_name": "Nombre del padre/madre",
            "last_name": "Apellidos",
            "dni": "DNI/NIE",
            "phone": "Teléfono",
            "email": "Email",
            "iban": "IBAN (opcional)",
        }

    #: True after `clean_email()` when the submitted address already belongs to
    #: another Parent row. Read by `ParentCreateView.form_valid` to warn.
    email_collides_with_other_family = False

    def clean_dni(self):
        dni = self.cleaned_data.get("dni", "").upper().strip()
        if dni and len(dni) < 8:
            raise forms.ValidationError("El DNI debe tener al menos 8 caracteres")
        return dni

    def clean_email(self):
        """DETECT a duplicate portal email — deliberately without rejecting it.

        Raising here would break the second-sibling flow, which re-types the
        SAME parent (same DNI, therefore the same email) and relies on
        `ParentCreateView.form_valid` reusing the existing row: an email error
        would make the form invalid and `form_valid` would never run. Two
        tutors legitimately sharing one mailbox is also a real case, and this
        is the phone-call-in-progress screen — so the app UI warns and
        continues, and the flag is what it warns from. `ParentAdminForm` makes
        the stricter call for a deliberate admin edit.
        """
        email = (self.cleaned_data.get("email") or "").strip()
        self.email_collides_with_other_family = bool(
            email and Parent.other_families_sharing_email(email, exclude_pk=self.instance.pk).exists()
        )
        return email

    def validate_unique(self):
        """Skip the DNI uniqueness check so the view can handle duplicates.

        `Parent.dni` is unique, so a repeat DNI made the form invalid and
        `ParentCreateView.form_valid()` never ran — which meant its "this
        parent already exists, taking you to create their child" redirect was
        unreachable and the user just saw a raw field error. The view looks the
        existing parent up and reuses it, which is what the academy actually
        wants when a second sibling is enrolled.
        """
        exclude = self._get_validation_exclusions()
        exclude.add("dni")
        try:
            self.instance.validate_unique(exclude=exclude)
        except forms.ValidationError as e:
            self._update_errors(e)


# Formset - Herencia de forms
ParentFormSet = inlineformset_factory(
    Student,
    Student.parents.through,  # StudentParent
    fields=("parent",),
    extra=1,
    can_delete=True,
    widgets={"parent": forms.Select(attrs={"class": "form-control"})},
)
