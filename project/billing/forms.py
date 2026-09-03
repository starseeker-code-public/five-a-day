from datetime import date
from decimal import Decimal

from django import forms

# Unified enrollment plan choices
ENROLLMENT_PLAN_CHOICES = [
    ("monthly_full", "Mensual (2 días/semana)"),
    ("monthly_part", "Mensual (1 día/semana)"),
    ("quarterly", "Trimestral"),
]


class EnrollmentForm(forms.Form):
    """
    Simplified enrollment form.
    Children: choose plan (monthly 2d, 1d, quarterly) + checkboxes for discounts.
    Adults: plan is fixed (handled in view). Special checkbox enables manual price.
    """

    enrollment_plan = forms.ChoiceField(
        choices=ENROLLMENT_PLAN_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-control", "id": "id_enrollment_plan"}),
        label="Tipo de matrícula",
    )
    # The day the student actually STARTS, which need not be the day the ficha is
    # created: a family signing up today for a 1 November start is billed from
    # November. It becomes `Enrollment.enrollment_date`, so the academic year,
    # the first billing period and its proration all derive from it. Blank means
    # today. `initial` is a callable so it is evaluated per render, not at import.
    start_date = forms.DateField(
        required=False,
        initial=date.today,
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={"class": "form-control", "type": "date", "id": "id_start_date"},
        ),
        label="Fecha de inicio",
    )
    has_language_cheque = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "id_has_language_cheque"}),
        label="Cheque idioma",
    )
    is_sibling_discount = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "id_is_sibling_discount"}),
        label="Descuento hermano",
    )
    sibling_id = forms.IntegerField(
        required=False,
        widget=forms.HiddenInput(attrs={"id": "id_sibling_id"}),
    )
    # Forces the returning-student matrícula ("Antiguo alumno") even when the
    # Student row carries no prior Enrollment — a student re-joining after years
    # away, or one promoted off the waiting list, is a fresh row to the service's
    # auto-detection. Checking it never *removes* the discount the auto-detection
    # would grant on its own.
    is_returning_student = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "id_is_returning_student"}),
        label="Antiguo alumno",
    )
    is_special = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "id_is_special"}),
        label="Precio especial",
    )
    manual_amount = forms.DecimalField(
        required=False,
        min_value=Decimal("0.01"),
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "step": "0.01",
                "min": "0.01",
                "placeholder": "Precio personalizado",
                "id": "id_manual_amount",
            }
        ),
        label="Precio manual (€)",
    )
    # Optional second price: `manual_amount` is the RECURRING fee (per month, or per
    # quarter for a quarterly plan), which says nothing about the one-time matrícula.
    # Leave this blank and the standard matrícula is charged, returning-student
    # discount included; fill it and it is charged verbatim.
    special_enrollment_fee = forms.DecimalField(
        required=False,
        min_value=Decimal("0.01"),
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "step": "0.01",
                "min": "0.01",
                "placeholder": "Dejar vacío para la matrícula estándar",
                "id": "id_special_enrollment_fee",
            }
        ),
        label="Matrícula especial (€)",
    )

    def __init__(self, *args, current_start=None, **kwargs):
        """`current_start` is the live enrollment's start date, when editing.

        The date-window validation below must always accept the value already
        stored — otherwise an ordinary edit of an enrollment from a past course
        (whose pre-filled start date POSTs back unchanged) would be rejected by
        a rule meant to catch typos on NEW enrollments.
        """
        super().__init__(*args, **kwargs)
        self._current_start = current_start

    def clean_start_date(self):
        start = self.cleaned_data.get("start_date")
        if not start or start == self._current_start:
            return start

        # Bound to the courses currently in play (`relevant_academic_years` —
        # one year most of the time, two in the May-August overlap). Unbounded,
        # a mistyped year filed the enrollment under an old `academic_year`,
        # which dropped the student out of the list views AND back-filled a
        # year of already-overdue payments that the reminder cron then chased.
        from billing.models import relevant_academic_years

        years = relevant_academic_years()
        # From 1 July before the earliest relevant course (a July/August start
        # belongs to the course beginning that September) to the end of the
        # summer after the latest one.
        lower = date(int(years[0].split("-")[0]), 7, 1)
        upper = date(int(years[-1].split("-")[1]), 8, 31)
        if not (lower <= start <= upper):
            raise forms.ValidationError(
                f"La fecha de inicio debe estar dentro del curso actual "
                f"({lower.strftime('%d/%m/%Y')} – {upper.strftime('%d/%m/%Y')}). "
                f"Revisa el año: ¿era {start.strftime('%d/%m/%Y')}?"
            )
        return start

    def clean(self):
        cleaned_data = super().clean()
        is_special = cleaned_data.get("is_special")
        manual_amount = cleaned_data.get("manual_amount")
        if is_special and not manual_amount:
            raise forms.ValidationError("Debes especificar un precio manual para matrícula especial")
        # Silently ignoring it would charge the standard matrícula while the admin
        # believes they set one — say so instead.
        if cleaned_data.get("special_enrollment_fee") and not is_special:
            raise forms.ValidationError("Marca «Precio especial» para fijar una matrícula personalizada")
        return cleaned_data

    def create_enrollment(self, student, is_adult=False):
        """Create and save an Enrollment from form data.
        Delegates to EnrollmentService for business logic."""
        from billing.services.enrollment_service import EnrollmentService

        enrollment_data = {
            "enrollment_plan": self.cleaned_data.get("enrollment_plan", "monthly_full"),
            "start_date": self.cleaned_data.get("start_date"),
            "has_language_cheque": self.cleaned_data.get("has_language_cheque", False),
            "is_sibling_discount": self.cleaned_data.get("is_sibling_discount", False),
            "is_special": self.cleaned_data.get("is_special", False),
            "manual_amount": self.cleaned_data.get("manual_amount"),
            "is_returning_student": self.cleaned_data.get("is_returning_student", False),
        }
        return EnrollmentService.create_enrollment(student, enrollment_data, is_adult=is_adult)
