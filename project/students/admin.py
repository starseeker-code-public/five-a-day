from django import forms
from django.contrib import admin, messages
from django.db.models import Count, Q
from django.utils import timezone

from students.forms import (
    PORTAL_EMAIL_COLLISION_ERROR,
    PORTAL_EMAIL_COLLISION_WARNING,
    GroupCapacityMixin,
)
from students.models import Group, Parent, Student, StudentParent, Teacher


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    """Teacher records, WITHOUT their second factor.

    Registered bare (`admin.site.register(Teacher)`) until now, which meant every
    field rendered as an editable input — including `two_factor_secret`. That is
    the TOTP seed in plain text: any admin could read a colleague's, enrol it in
    their own authenticator and hold a second factor for that account
    indefinitely, and anyone who borrowed one admin session could harvest the lot
    and keep access after the session died. `two_factor_backup_codes` (the
    hashes) rendered too.

    Storing the seed unencrypted is a defensible call — it is shared with the
    authenticator app by design, and a DB compromise hands the attacker
    everything anyway. Printing it on a web page an admin can open is a
    different question, and the answer is no: the credentials are excluded from
    the form outright rather than made read-only, since a read-only field is
    still rendered.

    `two_factor_enabled` stays visible but read-only. Turning it off from here
    would leave a stale secret behind and silently drop an admin to one factor;
    `manage.py reset_two_factor <email>` is the supported recovery path and it
    re-issues codes properly.
    """

    list_display = ["full_name", "email", "phone", "admin", "active", "two_factor_enabled", "login_account"]
    list_filter = ["active", "admin", "two_factor_enabled"]
    search_fields = ["first_name", "last_name", "email"]
    readonly_fields = ["two_factor_enabled", "login_account", "created_at", "updated_at"]
    # Never rendered, not even disabled: a read-only field still prints its value.
    exclude = ["two_factor_secret", "two_factor_backup_codes", "two_factor_last_counter"]

    fieldsets = (
        ("Datos personales", {"fields": ("first_name", "last_name", "email", "phone")}),
        ("Acceso", {"fields": ("active", "admin", "user", "login_account")}),
        (
            "Segundo factor",
            {
                "fields": ("two_factor_enabled",),
                "description": (
                    "El secreto TOTP y los códigos de respaldo no se muestran aquí. "
                    "Para reiniciar el segundo factor de una cuenta: "
                    "<code>manage.py reset_two_factor &lt;email&gt;</code>."
                ),
            },
        ),
        ("Sistema", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Nombre", ordering="last_name")
    def full_name(self, obj):
        return obj.full_name

    @admin.display(description="Cuenta de acceso")
    def login_account(self, obj):
        """Whether this Teacher can actually sign in.

        A Teacher row on its own is just a record: authentication goes through
        the linked `auth.User`, and `/password-reset/` matches on that user too.
        Without it the account can neither log in nor be activated, and nothing
        on the page said so.
        """
        if obj.user_id:
            return f"✓ {obj.user.username}"
        return "✗ sin cuenta — no puede iniciar sesión"

    def save_model(self, request, obj, form, change):
        """Create the linked `auth.User` the same way every other path does.

        Adding a Teacher here used to produce a row with `user=None`: they could
        not log in, and `/password-reset/` silently sent nothing because no user
        matched the address. `create_teacher` in the app UI already calls this;
        the admin was the one entry point that did not.
        """
        super().save_model(request, obj, form, change)
        obj.ensure_user()


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ["group_name", "teacher", "max_students", "enrolled_count", "available_spots", "active"]
    list_filter = ["active", "teacher"]
    search_fields = ["group_name"]
    # `teacher` is rendered per row, and `enrolled_count` / `available_spots`
    # each counted the group's students with their own query — three round trips
    # per row (39 for 13 groups). The join and the annotation collapse it to one.
    list_select_related = ["teacher"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            # `is_waiting=False` matches Group.enrolled_count and
            # group_capacity_summary — without it, waiting-list students (who
            # carry their preferred group FK) counted as enrolled, so a group
            # with free places read "0 plazas libres" and the admin refused to
            # promote anyone into it.
            .annotate(
                _enrolled=Count(
                    "students",
                    filter=Q(students__active=True, students__is_waiting=False),
                    distinct=True,
                )
            )
        )

    @admin.display(description="Matriculados", ordering="_enrolled")
    def enrolled_count(self, obj):
        return obj._enrolled

    @admin.display(description="Plazas libres")
    def available_spots(self, obj):
        """0 in `max_students` means "no cap" — not "full"."""
        if not obj.max_students:
            return "sin límite"
        return max(obj.max_students - obj._enrolled, 0)


# Students and parents
class StudentParentInline(admin.TabularInline):
    model = StudentParent
    extra = 1  # Number of empty forms to display
    autocomplete_fields = ["parent"]


class ParentStudentInline(admin.TabularInline):
    model = StudentParent
    extra = 1
    autocomplete_fields = ["student"]


class StudentAdminForm(GroupCapacityMixin, forms.ModelForm):
    """Applies the group-capacity cap the app's own forms apply.

    `StudentAdmin` set no `form`, so the change/add form was an auto-generated
    ModelForm with `group` freely editable and no cap at all — an admin edit
    silently over-filled a group past `max_students`, and a 9th student in an
    8-place group then made `Group.is_full` refuse every waiting-list promotion
    into it. The old `StudentForm.clean_group` claimed in its own docstring that
    the admin was covered; it never was.

    Same shape as `core.admin.ScheduleSlotAdminForm`, and for the same reason:
    one validator (`Group.has_room_for`), reached from both the API/app path and
    the admin, so the two cannot drift apart. `is_waiting` is in the Status
    fieldset, so the waiting-list exemption reads straight off `cleaned_data`
    here — no `waiting=` override is needed.
    """

    class Meta:
        model = Student
        fields = "__all__"


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    form = StudentAdminForm
    list_display = ["first_name", "last_name", "group", "active", "is_adult", "is_waiting", "waiting_priority"]
    list_filter = ["group", "active", "is_adult", "is_waiting", "waiting_priority", "gdpr_signed"]
    search_fields = ["first_name", "last_name"]
    inlines = [StudentParentInline]
    readonly_fields = ["waiting_since"]
    list_select_related = ["group"]

    # Legends are USER-FACING, so they are in Spanish like every other label in
    # the UI — the same call v1.20.0 made for the choice labels.
    fieldsets = (
        (
            "Datos personales",
            {
                "fields": ("first_name", "last_name", "birth_date", "gender"),
                "description": (
                    "La fecha de nacimiento es opcional: un alta de lista de espera se toma por teléfono "
                    "y puede no tenerla. Sin ella no se calcula la edad ni se envía el email de cumpleaños."
                ),
            },
        ),
        ("Datos escolares", {"fields": ("school", "course", "group")}),
        # An adult student has no Parent row, so these two fields are the ONLY
        # way to contact them — and neither was on the form.
        (
            "Contacto",
            {
                "fields": ("is_adult", "email", "phone"),
                "description": "Un alumno adulto no tiene padre/tutor: su email y teléfono son el único contacto.",
            },
        ),
        ("Salud y preferencias", {"fields": ("allergies", "gdpr_signed", "observations")}),
        (
            "Situación",
            {
                "fields": (
                    "active",
                    "is_waiting",
                    "waiting_since",
                    "waiting_priority",
                    # The waiting list is taken over the phone and its contact
                    # lives here, NOT in a Parent row. Unreachable until now, so
                    # a wrong number could not be corrected anywhere.
                    "waiting_contact_name",
                    "waiting_contact_phone",
                    "withdrawal_date",
                    "withdrawal_reason",
                )
            },
        ),
    )


class ParentAdminForm(forms.ModelForm):
    """Refuses an email that would lock two families out of the parent portal.

    `Parent.email` is NOT unique and `_parent_by_email` deliberately refuses an
    ambiguous match (serving one family another family's payment history is the
    worse failure), so two rows carrying one address lock BOTH out of login and
    out of "¿Has olvidado tu contraseña?". `ParentCreateView` warns about it;
    the admin had no check whatsoever and left `email` freely editable.

    Deliberately HARDER than the app UI. The app's warning is a
    phone-call-in-progress compromise — and its form must stay valid so
    `form_valid` can reuse an existing parent for a second sibling. An admin
    editing this screen is doing so deliberately, with time to pick another
    address, and the cost of getting it wrong is two real families unable to
    reach their own invoices. So a *new or changed* email that collides is
    refused outright.

    A collision that ALREADY exists with the email left untouched is allowed
    through: refusing it would make both colliding rows unsaveable, so neither
    could ever be corrected — including by an admin trying to fix exactly this.
    `ParentAdmin.save_model` warns in that case instead.
    """

    class Meta:
        model = Parent
        fields = "__all__"

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip()
        if not email:
            return email
        # `self.instance` still holds the DB row here: `_post_clean` (which
        # copies cleaned_data onto it) runs after field cleaning.
        previous = (self.instance.email or "").strip()
        if self.instance.pk is not None and previous.lower() == email.lower():
            return email
        if Parent.other_families_sharing_email(email, exclude_pk=self.instance.pk).exists():
            raise forms.ValidationError(PORTAL_EMAIL_COLLISION_ERROR)
        return email


@admin.register(Parent)
class ParentAdmin(admin.ModelAdmin):
    form = ParentAdminForm
    list_display = ["first_name", "last_name", "dni", "phone", "email", "sms_opt_in", "portal_status"]
    list_filter = ["sms_opt_in"]
    search_fields = ["first_name", "last_name", "dni", "email"]
    inlines = [ParentStudentInline]
    # The credential itself is NEVER shown or editable (it is a hash, and a
    # revoke path that printed it would be a hazard); but the invite/reset
    # TIMESTAMPS must be reachable. Without them there was no way to see that a
    # family had been invited (the once-only guard is keyed on
    # `portal_invite_sent_at`) or to fix a bounced invite, and no revoke path at
    # all short of a DB shell. `portal_actions` provides re-invite + revoke.
    readonly_fields = [
        "portal_status",
        "portal_invite_sent_at",
        "temporary_password_issued_at",
        "portal_credential_changed_at",
    ]
    actions = ["revoke_portal_access", "resend_portal_invitation"]

    # Legends in Spanish, like every other user-facing string in the UI.
    fieldsets = (
        ("Datos personales", {"fields": ("first_name", "last_name", "dni")}),
        # `sms_opt_in` gates every SMS in `comms.tasks`, and there was no screen
        # anywhere in the app — admin included — that could grant or revoke it.
        (
            "Datos de contacto",
            {
                "fields": ("phone", "email", "iban", "sms_opt_in"),
                "description": (
                    "«SMS opt-in» es el consentimiento para recibir avisos por SMS. "
                    "El email es la identidad de acceso al portal de familias: dos tutores con el mismo "
                    "email no podrán entrar, así que cada uno necesita el suyo."
                ),
            },
        ),
        (
            "Portal de familias",
            {
                "fields": (
                    "portal_status",
                    "portal_invite_sent_at",
                    "temporary_password_issued_at",
                    "portal_credential_changed_at",
                ),
                "description": (
                    "La contraseña nunca se muestra. Usa las acciones de la lista para reenviar "
                    "la invitación o revocar el acceso."
                ),
            },
        ),
    )

    @admin.display(description="Portal")
    def portal_status(self, obj):
        if obj.password:
            return "Con contraseña"
        if obj.temporary_password:
            return "Invitado (temporal)"
        return "Sin acceso"

    def save_model(self, request, obj, form, change):
        """Warn about a duplicate portal email the form let through.

        `ParentAdminForm` refuses a NEW collision; a pre-existing one is
        deliberately still saveable (otherwise neither of the two rows could be
        corrected), so it has to be surfaced somewhere — an admin who cannot see
        the problem cannot fix it, and the symptom the families report is a
        generic "email o contraseña incorrectos".
        """
        super().save_model(request, obj, form, change)
        if obj.email and Parent.other_families_sharing_email(obj.email, exclude_pk=obj.pk).exists():
            self.message_user(request, PORTAL_EMAIL_COLLISION_WARNING, level=messages.WARNING)

    @admin.action(description="Revocar acceso al portal (borra la contraseña)")
    def revoke_portal_access(self, request, queryset):
        count = 0
        for parent in queryset:
            parent.password = ""
            parent.temporary_password = ""
            parent.temporary_password_issued_at = None
            # Bump so any live session for this family is invalidated at once.
            parent.portal_credential_changed_at = timezone.now()
            parent.save(
                update_fields=[
                    "password",
                    "temporary_password",
                    "temporary_password_issued_at",
                    "portal_credential_changed_at",
                    "updated_at",
                ]
            )
            count += 1
        self.message_user(request, f"Acceso al portal revocado para {count} familia(s).")

    @admin.action(description="Reenviar invitación al portal (nueva contraseña temporal)")
    def resend_portal_invitation(self, request, queryset):
        from core.views.parent_portal import send_portal_temporary_password

        sent = skipped = 0
        for parent in queryset:
            if not parent.email:
                skipped += 1
                continue
            # STAMP the once-only guard, do not clear it. This action calls the
            # DIRECT sender, not `send_portal_invitation_once`, so the guard was
            # never in its way — clearing it did nothing for the resend and
            # disarmed the guard permanently, because nothing on this path ever
            # re-stamps it (only `send_portal_invitation_once` does, and the
            # task only writes the temporary password). The family then enrols a
            # second child, `ParentCreateView`'s existing-DNI branch calls
            # `send_portal_invitation_once`, the guard is open — so a duplicate
            # invitation goes out AND `issue_temporary_password` rotates the
            # hash, silently killing the temporary password the family is
            # holding from THIS resend. It also blanked the readonly "invitación
            # enviada" display, so the screen read "never invited" immediately
            # after an invitation was sent.
            #
            # Stamped BEFORE the send is queued, like `send_portal_invitation_once`:
            # a duplicate invite is worse than a missed one, which the family can
            # recover from "¿Has olvidado tu contraseña?".
            parent.portal_invite_sent_at = timezone.now()
            parent.save(update_fields=["portal_invite_sent_at", "updated_at"])
            if send_portal_temporary_password(request, parent, reset=True):
                sent += 1
            else:
                skipped += 1
        self.message_user(request, f"Invitaciones reenviadas: {sent}. Omitidas (sin email o error): {skipped}.")


@admin.register(StudentParent)
class StudentParentAdmin(admin.ModelAdmin):
    list_display = ["student", "parent"]
    list_filter = ["student__group"]
    autocomplete_fields = ["student", "parent"]
    list_select_related = ["student", "parent"]
