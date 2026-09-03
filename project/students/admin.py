from django.contrib import admin
from django.db.models import Count, Q

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


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ["first_name", "last_name", "group", "active", "is_adult", "is_waiting", "waiting_priority"]
    list_filter = ["group", "active", "is_adult", "is_waiting", "waiting_priority", "gdpr_signed"]
    search_fields = ["first_name", "last_name"]
    inlines = [StudentParentInline]
    readonly_fields = ["waiting_since"]
    list_select_related = ["group"]

    fieldsets = (
        ("Personal Information", {"fields": ("first_name", "last_name", "birth_date", "gender")}),
        ("School Information", {"fields": ("school", "course", "group")}),
        # An adult student has no Parent row, so these two fields are the ONLY
        # way to contact them — and neither was on the form.
        (
            "Contact",
            {
                "fields": ("is_adult", "email", "phone"),
                "description": "Un alumno adulto no tiene padre/tutor: su email y teléfono son el único contacto.",
            },
        ),
        ("Health & Preferences", {"fields": ("allergies", "gdpr_signed", "observations")}),
        (
            "Status",
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


@admin.register(Parent)
class ParentAdmin(admin.ModelAdmin):
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

    fieldsets = (
        ("Personal Information", {"fields": ("first_name", "last_name", "dni")}),
        # `sms_opt_in` gates every SMS in `comms.tasks`, and there was no screen
        # anywhere in the app — admin included — that could grant or revoke it.
        (
            "Contact Information",
            {
                "fields": ("phone", "email", "iban", "sms_opt_in"),
                "description": "«SMS opt-in» es el consentimiento para recibir avisos por SMS.",
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

    @admin.action(description="Revocar acceso al portal (borra la contraseña)")
    def revoke_portal_access(self, request, queryset):
        from django.utils import timezone

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
            # Clear the once-only guard so the invitation actually re-fires.
            parent.portal_invite_sent_at = None
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
