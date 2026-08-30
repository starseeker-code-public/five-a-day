from django import forms
from django.forms import ModelForm, inlineformset_factory

from students.models import Group, Parent, Student

DATE_INPUT_FORMATS = ["%Y-%m-%d", "%d/%m/%Y"]


class StudentForm(ModelForm):
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["birth_date"].input_formats = DATE_INPUT_FORMATS
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

    def clean_dni(self):
        dni = self.cleaned_data.get("dni", "").upper().strip()
        if dni and len(dni) < 8:
            raise forms.ValidationError("El DNI debe tener al menos 8 caracteres")
        return dni

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
