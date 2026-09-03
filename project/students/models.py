import functools
import secrets

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.validators import EmailValidator
from django.db import models
from django.db.models.functions import Upper
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

# What `Parent.authenticate_portal` returns, so callers branch on a name rather
# than on which of two hashes happened to match.
PORTAL_AUTH_PASSWORD = "password"
PORTAL_AUTH_TEMPORARY = "temporary"

#: Alphabet for generated temporary passwords. Deliberately excludes the
#: characters families misread when copying one out of an email — 0/O, 1/l/I —
#: because the whole point of a temporary password is that it gets typed in by
#: hand exactly once.
_TEMP_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


@functools.cache
def _no_credential_dummy_hash() -> str:
    """Dummy hash for equalising failed-login work. Never a usable password.

    Computed LAZILY (and memoised): evaluated at module import, this ran a full
    PBKDF2 (~0.3 s of CPU, 1.2M iterations) in every process that imports the
    models — each Gunicorn worker, every manage.py invocation, all the Cloud Run
    Jobs — most of which never authenticate a parent at all. The test settings
    swap in a fast hasher, which is exactly why the suite never noticed.
    """
    return make_password("fad-portal-no-credential-set")


def burn_portal_login_work(raw_password: str) -> None:
    """Spend the same hashing work a failed `authenticate_portal` costs.

    For callers that resolved NO parent for the submitted address: without this,
    an unknown email returned in milliseconds while a known one paid two hash
    verifications — a remote timing oracle for exactly which addresses belong to
    families of the academy (the fact the unified error message exists to hide).
    """
    check_password(raw_password, _no_credential_dummy_hash())
    check_password(raw_password, _no_credential_dummy_hash())


def generate_temporary_password() -> str:
    """
    A 12-character random password, grouped as ``xxxx-xxxx-xxxx``.

    ~68 bits over the unambiguous alphabet above, which is far past anything the
    5-per-minute login throttle would let an attacker reach. Grouped with dashes
    because it is read off a phone screen and typed into a laptop.
    """
    body = "".join(secrets.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(12))
    return f"{body[:4]}-{body[4:8]}-{body[8:]}"


class Teacher(models.Model):
    last_name = models.CharField(max_length=100)
    first_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True, validators=[EmailValidator()])
    phone = models.CharField(max_length=20, blank=True)
    active = models.BooleanField(default=True)
    admin = models.BooleanField(default=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="teacher",
        help_text="Django auth user — login identity + hashed password for this teacher.",
    )
    # v1.13 — TOTP two-factor authentication (admins only in practice)
    two_factor_secret = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Base32-encoded TOTP shared secret. Empty when 2FA is not set up.",
    )
    two_factor_enabled = models.BooleanField(
        default=False,
        help_text="True once the user has confirmed enrolment by entering a valid TOTP code.",
    )
    two_factor_backup_codes = models.JSONField(
        default=list,
        blank=True,
        help_text="Hashed one-time backup codes. Consumed on use.",
    )
    two_factor_last_counter = models.BigIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Highest TOTP time-step already accepted. Rejects replay of a code "
            "that is still inside its validity window (RFC 6238 §5.2)."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "teachers"
        verbose_name = "Profesor"
        verbose_name_plural = "Profesores"
        indexes = [
            # No `Index(fields=["email"])`: the field is `unique=True`, and a
            # unique constraint IS a b-tree index in Postgres. Declaring both
            # built two identical indexes on one column — see the note on
            # `Payment.Meta.indexes` for why that is not free.
            models.Index(fields=["active"]),
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    def ensure_user(self, password=None):
        """
        Get-or-create the linked auth.User, keeping email/name and staff/superuser
        flags in sync with this Teacher. If `password` is provided it is set (hashed);
        otherwise the user gets an unusable password and must use the password-reset
        flow to activate their account.

        Returns the linked User instance.
        """
        from django.contrib.auth import get_user_model

        User = get_user_model()

        if self.user_id:
            user = self.user
            dirty = False
            if user.email != self.email:
                user.email = self.email
                user.username = self.email
                dirty = True
            if user.first_name != self.first_name:
                user.first_name = self.first_name
                dirty = True
            if user.last_name != self.last_name:
                user.last_name = self.last_name
                dirty = True
            if user.is_staff != self.admin:
                user.is_staff = self.admin
                dirty = True
            if user.is_superuser != self.admin:
                user.is_superuser = self.admin
                dirty = True
            if user.is_active != self.active:
                # `active` is the offboarding switch (the admin fieldset is
                # literally titled "Acceso"). Unmirrored, unticking it removed
                # the teacher from every dropdown and left their login working.
                user.is_active = self.active
                dirty = True
            if password is not None:
                user.set_password(password)
                dirty = True
            if dirty:
                user.save()
            return user

        # Either link an existing User with this email, or create a new one.
        user, created = User.objects.get_or_create(
            username=self.email,
            defaults={
                "email": self.email,
                "first_name": self.first_name,
                "last_name": self.last_name,
                "is_staff": self.admin,
                "is_superuser": self.admin,
                "is_active": self.active,
            },
        )
        if not created:
            # Sync fields on the existing user record.
            user.email = self.email
            user.first_name = self.first_name
            user.last_name = self.last_name
            user.is_staff = self.admin
            user.is_superuser = self.admin
            user.is_active = self.active

        if password is not None:
            user.set_password(password)
        elif created:
            user.set_unusable_password()
        user.save()

        # Link back without retriggering Teacher.save() signals.
        Teacher.objects.filter(pk=self.pk).update(user=user)
        self.user = user
        return user


@receiver(post_save, sender=Teacher)
def _sync_linked_user_flags(sender, instance, **kwargs):
    """Mirror Teacher.admin / email / name onto the linked auth.User when present."""
    if not instance.user_id:
        return
    user = instance.user
    dirty = False
    if user.is_staff != instance.admin:
        user.is_staff = instance.admin
        dirty = True
    if user.is_superuser != instance.admin:
        user.is_superuser = instance.admin
        dirty = True
    if user.is_active != instance.active:
        user.is_active = instance.active
        dirty = True
    if user.email != instance.email:
        user.email = instance.email
        user.username = instance.email
        dirty = True
    if user.first_name != instance.first_name:
        user.first_name = instance.first_name
        dirty = True
    if user.last_name != instance.last_name:
        user.last_name = instance.last_name
        dirty = True
    if dirty:
        user.save()


@receiver(post_delete, sender=Teacher)
def _deactivate_orphaned_user(sender, instance, **kwargs):
    """Deleting a Teacher must not ESCALATE its login.

    `Teacher.user` survives the delete (the FK is on Teacher), and an auth.User
    with no Teacher row used to bypass both the 2FA requirement and the
    non-admin URL whitelist (each is keyed off the Teacher). Deactivate the
    orphaned account; an admin can re-link or re-enable it deliberately from
    /admin/ if the delete was a mistake.
    """
    if instance.user_id:
        from django.contrib.auth import get_user_model

        get_user_model().objects.filter(pk=instance.user_id, is_active=True).update(is_active=False)


class Group(models.Model):
    group_name = models.CharField(max_length=100, unique=True)
    color = models.CharField(max_length=7, default="#6366f1")
    teacher = models.ForeignKey(Teacher, on_delete=models.PROTECT, related_name="groups")
    max_students = models.PositiveIntegerField(
        default=8,
        verbose_name="Cupo máximo",
        help_text="Soft limit on active enrolled students. 0 means no cap.",
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "groups"
        verbose_name = "Grupo"
        verbose_name_plural = "Grupos"
        # No `indexes`: `group_name` is unique and `teacher` is a ForeignKey, so
        # both columns are already indexed — by the unique constraint and by
        # Django's FK default. See the note on `Payment.Meta.indexes`.

    def __str__(self):
        return self.group_name

    #: Annotation aliases that already carry this group's occupancy, in
    #: preference order. `core.views.waiting_list.group_capacity_summary()`
    #: annotates `enrolled` / `waiting`; `GroupAdmin.get_queryset` annotates
    #: `_enrolled`. Listed here so the properties below can be free when the
    #: queryset already paid for the count, and still correct on a bare object.
    ENROLLED_ANNOTATIONS = ("enrolled", "_enrolled")
    WAITING_ANNOTATIONS = ("waiting", "_waiting")

    def _annotated(self, names):
        """First of `names` present on this INSTANCE, or None.

        Reads `__dict__` rather than `getattr`: a queryset annotation lands
        there, while `getattr` would walk the class and could hand back the
        property itself or a related manager that happens to share the name.
        """
        for name in names:
            value = self.__dict__.get(name)
            if value is not None:
                return value
        return None

    def occupied_count(self, exclude_student_pk=None) -> int:
        """Fresh COUNT of the students occupying a place, ignoring annotations.

        `exclude_student_pk` discounts the student currently being saved, which
        is what lets an edit that keeps a student in an already-full group
        through — they are one of the occupants being counted.
        """
        qs = self.students.filter(active=True, is_waiting=False)
        if exclude_student_pk is not None:
            qs = qs.exclude(pk=exclude_student_pk)
        return qs.count()

    def has_room_for(self, *, exclude_student_pk=None) -> bool:
        """Whether one more student can take a place in this group.

        THE capacity predicate. `students.forms` (and therefore both the app's
        student forms and the admin's) validate against this one method, so the
        cap cannot be enforced two different ways — the rule used to live in
        `StudentForm.clean_group` only, which the admin never went through.
        `max_students == 0` means "no cap", not "no room".
        """
        if not self.max_students:
            return True
        if exclude_student_pk is None:
            occupied = self.enrolled_count
        else:
            occupied = self.occupied_count(exclude_student_pk=exclude_student_pk)
        return occupied < self.max_students

    @property
    def enrolled_count(self):
        """Active, non-waiting students currently occupying a spot in this group.

        Prefers an annotation the queryset already resolved (see
        `ENROLLED_ANNOTATIONS`) so a page that went through
        `group_capacity_summary()` pays nothing here. Without that, one template
        row touching `enrolled_count`, `waiting_count`, `available_spots` and
        `is_full` cost FOUR queries — the last two recompute this one.
        """
        annotated = self._annotated(self.ENROLLED_ANNOTATIONS)
        if annotated is not None:
            return annotated
        return self.occupied_count()

    @property
    def waiting_count(self):
        """Students on the waiting list whose preferred group is this one."""
        annotated = self._annotated(self.WAITING_ANNOTATIONS)
        if annotated is not None:
            return annotated
        return self.students.filter(active=True, is_waiting=True).count()

    @property
    def available_spots(self):
        """Remaining spots; None when the group has no cap (max_students == 0)."""
        if not self.max_students:
            return None
        return max(self.max_students - self.enrolled_count, 0)

    @property
    def is_full(self):
        """True only when a cap is set and enrolled_count has reached it."""
        return not self.has_room_for()


class Parent(models.Model):
    last_name = models.CharField(max_length=100)
    first_name = models.CharField(max_length=100)
    dni = models.CharField(max_length=20, unique=True)
    phone = models.CharField(max_length=20)
    email = models.EmailField()
    iban = models.CharField(max_length=34, blank=True)  # International Bank Account Number
    sms_opt_in = models.BooleanField(
        default=False,
        verbose_name="SMS opt-in",
        help_text="v1.8: True if the parent has opted in to SMS notifications (payment reminders, urgent comms).",
    )
    # Parent-portal credential (v1.27). Deliberately a hashed password field on
    # Parent rather than a linked `auth.User`: `core.views.auth._authenticate_teacher`
    # authenticates ANY auth.User, so giving a family an auth.User would hand
    # them a staff login into the academy's admin app. The portal keeps its own
    # session (`parent_id`), so it never touches django.contrib.auth at all.
    # Blank until the parent follows the emailed set-password link.
    password = models.CharField(
        max_length=128,
        blank=True,
        verbose_name="Contraseña del portal",
        help_text="Hash de la contraseña del portal de familias. Vacío hasta que el padre/tutor la crea.",
    )
    # Hash of a one-off password emailed to the family — the way in when they
    # have not onboarded yet, or have forgotten the password they chose.
    #
    # A SECOND field and not an overwrite of `password`: "he olvidado mi
    # contraseña" is unauthenticated, so writing over the real credential would
    # let anyone who knows a family's address lock them out. Both are accepted
    # at login until the family sets their own, which clears this one.
    temporary_password = models.CharField(
        max_length=128,
        blank=True,
        verbose_name="Contraseña temporal",
        help_text="Hash de la contraseña temporal enviada por email. Se borra en cuanto el padre/tutor elige la suya.",
    )
    temporary_password_issued_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Contraseña temporal enviada",
    )
    # Once-only guard for the portal invitation. A family with three children
    # must receive exactly ONE invite, so the send is keyed on this timestamp
    # and not on the number of students linked.
    portal_invite_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Invitación al portal enviada",
    )
    # Bumped whenever the portal credential changes (set-password). Every portal
    # session stores the value it was opened with; a session older than this is
    # rejected, so changing the password logs out every OTHER device — the
    # portal equivalent of Django's session-auth-hash. Without it, a reset only
    # ended the browser that performed it and any other logged-in session
    # survived on its rolling 6-hour expiry.
    portal_credential_changed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Credencial del portal cambiada",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)  # Added for consistency

    class Meta:
        db_table = "parents"
        verbose_name = "Padre/Tutor"
        verbose_name_plural = "Padres y tutores"
        indexes = [
            # No `Index(fields=["dni"])` — the field is `unique=True`, which is
            # already a b-tree index. See the note on `Payment.Meta.indexes`.
            #
            # No plain `Index(fields=["email"])` either. Every lookup on this
            # column is `email__iexact` (`_parent_by_email`, the portal login,
            # `send_email`, `seed_demo_parents`) or a non-indexable
            # `exclude(email="")` / `email__icontains`, so a case-SENSITIVE
            # b-tree answered none of them — it only added write cost to every
            # Parent insert and update. The functional index below is the one
            # that earns its keep.
            # Case-insensitive lookups need their own index: the parent-portal
            # login resolves a family by `email__iexact`, which Postgres renders
            # as `UPPER(email::text) = UPPER(%s)`. A plain b-tree on `email`
            # cannot answer that — verified with `enable_seqscan=off`, where the
            # planner still chose a sequential scan over the whole table — so
            # every login attempt on a public, rate-limited endpoint scanned
            # `parents` end to end. `Upper("email")` matches the expression the
            # ORM actually emits; an index on `Lower("email")` would not.
            models.Index(Upper("email"), name="parents_email_upper_idx"),
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.dni})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def audit_label(self):
        """PII-minimised label for `AuditLog` (see `core.audit_signals`).

        `AuditLog.object_label` defaults to `str(instance)`, and this model's
        `__str__` embeds the DNI — the one field the audit allow-list goes out
        of its way to EXCLUDE from `changes`. Letting it back in through the
        label meant the DNI of every parent was written into a searchable log
        kept for two years, defeating the whole point of the allow-list. The
        pk plus the name identifies the row just as well.
        """
        return f"{self.full_name} (#{self.pk})"

    @classmethod
    def other_families_sharing_email(cls, email, *, exclude_pk=None):
        """Parents OTHER than `exclude_pk` that already use `email`.

        `Parent.email` is not unique, and `_parent_by_email` deliberately
        REFUSES an ambiguous match rather than serving one family another
        family's payment history — so two rows carrying one address lock BOTH
        of them out of portal login and out of password recovery.

        The single predicate behind every write path's duplicate check
        (`ParentForm` / `ParentCreateView` warn, `ParentAdminForm` refuses a new
        collision), so the app UI and the admin cannot disagree about what a
        collision is. Matched with `iexact` because that is how the login
        resolves it — `Ana@x.com` and `ana@x.com` are the same lockout.
        """
        email = (email or "").strip()
        if not email:
            return cls.objects.none()
        qs = cls.objects.filter(email__iexact=email)
        if exclude_pk is not None:
            qs = qs.exclude(pk=exclude_pk)
        return qs

    # ── Portal credential ───────────────────────────────────────────────────

    @property
    def has_portal_password(self) -> bool:
        """True once the parent has chosen a password of their own."""
        return bool(self.password)

    @property
    def has_temporary_password(self) -> bool:
        """True while an emailed temporary password is still outstanding."""
        return bool(self.temporary_password)

    def set_portal_password(self, raw_password: str, *, save: bool = True) -> None:
        """
        Store the parent's OWN password and retire any outstanding temporary
        one.

        Clearing `temporary_password` here is the whole reason the two are
        separate fields: the moment the family chooses a credential, the one
        sitting in their inbox stops working. Leave it set and every old
        recovery email remains a live key to the account.
        """
        self.password = make_password(raw_password)
        self.temporary_password = ""
        self.temporary_password_issued_at = None
        # Invalidate every existing portal session (see the field's comment).
        self.portal_credential_changed_at = timezone.now()
        if save:
            self.save(
                update_fields=[
                    "password",
                    "temporary_password",
                    "temporary_password_issued_at",
                    "portal_credential_changed_at",
                    "updated_at",
                ]
            )

    def issue_temporary_password(self, *, save: bool = True) -> str:
        """
        Generate, store (hashed) and RETURN a one-off password to email.

        The plaintext is returned exactly once and never persisted — the caller
        puts it in the email and drops it.

        Deliberately stored beside `password` rather than over it: "he olvidado
        mi contraseña" is an unauthenticated endpoint, so overwriting the real
        credential would let anyone who knows a family's address lock them out
        of their own payment history. Both work until the family picks a new
        password, at which point `set_portal_password` clears this one.

        It does NOT expire, by design — an expiring credential is the thing this
        flow exists to get rid of. The cost is that an unused temporary password
        stays valid in the family's mailbox until they set their own; that is
        why logging in with one forces an immediate change.
        """
        raw = generate_temporary_password()
        self.temporary_password = make_password(raw)
        self.temporary_password_issued_at = timezone.now()
        if save:
            self.save(update_fields=["temporary_password", "temporary_password_issued_at", "updated_at"])
        return raw

    def authenticate_portal(self, raw_password: str) -> str | None:
        """
        Check a submitted password against both credentials.

        Returns ``"password"`` when it matched the parent's own, ``"temporary"``
        when it matched an emailed one (the caller must then force a change),
        and ``None`` when it matched neither.

        A failed attempt costs the SAME hashing work whatever the parent's
        onboarding state — see the comment below. Callers that resolve no
        parent at all must call `burn_portal_login_work` to match.
        """

        def _persist(new_hash: str) -> None:
            # Transparent upgrade when the configured hasher changes.
            self.password = new_hash
            self.save(update_fields=["password", "updated_at"])

        # Every FAILED attempt costs exactly two hash verifications, whatever
        # credentials exist. The old shape charged 2 hashes to a family with its
        # own password, 1 to one still on the emailed temporary, and 1 to one
        # with neither — a ~300 ms tell for which families held an unopened
        # temporary password, the highest-value target this endpoint has.
        if self.password:
            if check_password(raw_password, self.password, setter=_persist):
                return PORTAL_AUTH_PASSWORD
        else:
            check_password(raw_password, _no_credential_dummy_hash())

        if self.temporary_password:
            if check_password(raw_password, self.temporary_password):
                return PORTAL_AUTH_TEMPORARY
        else:
            check_password(raw_password, _no_credential_dummy_hash())

        return None


class Student(models.Model):
    # Optional for the same reason `birth_date` and `group` are: a waiting-list
    # entry is taken over the phone with a first name and a number, and pressing
    # for surnames before the family has even been offered a place lost calls.
    # The full StudentForm still requires it when the student is enrolled.
    last_name = models.CharField(max_length=100, blank=True)
    first_name = models.CharField(max_length=100)
    # Optional so a waiting-list entry can be taken down from a phone call with
    # nothing but a name and a number. It is filled in when the student is
    # actually enrolled.
    birth_date = models.DateField(null=True, blank=True)
    GENDER_CHOICES = [
        ("m", "Masculino"),
        ("f", "Femenino"),
    ]
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, default="m", verbose_name="Género")
    is_adult = models.BooleanField(default=False, verbose_name="Estudiante adulto (18+)")
    email = models.EmailField(blank=True, verbose_name="Email (solo adultos)")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Teléfono (solo adultos)")
    school = models.CharField(max_length=200, blank=True)
    allergies = models.TextField(blank=True)
    gdpr_signed = models.BooleanField(default=False)
    # Nullable for the same reason as birth_date: a waiting-list entry may not
    # have a preferred group yet. `assign_from_waiting_list` already guarded
    # for this case defensively before the field allowed it.
    group = models.ForeignKey(Group, on_delete=models.PROTECT, related_name="students", null=True, blank=True)
    # v1.15 — captured on the short waiting-list form (and useful thereafter).
    course = models.CharField(
        max_length=60,
        blank=True,
        verbose_name="Curso",
        help_text="Curso escolar, p. ej. «3º Primaria». Texto libre.",
    )
    observations = models.TextField(
        blank=True,
        verbose_name="Observaciones",
        help_text="Notas libres: preferencia de horario, peticiones, etc.",
    )
    # Contact for a waiting-list entry. Deliberately NOT a Parent FK: Parent
    # requires a unique DNI, which is far more than we can ask for over the
    # phone. A real Parent record is created when the student is enrolled.
    waiting_contact_name = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Contacto (padre/madre)",
    )
    waiting_contact_phone = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Móvil de contacto",
    )
    parents = models.ManyToManyField(Parent, through="StudentParent", related_name="children")
    active = models.BooleanField(default=True)
    is_waiting = models.BooleanField(
        default=False,
        verbose_name="En lista de espera",
        help_text="True when the student is on the waiting list for their preferred group instead of enrolled.",
    )
    waiting_since = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="En espera desde",
        help_text="Auto-set the first time is_waiting is flipped on; used to prioritize assignments FIFO.",
    )
    waiting_priority = models.BooleanField(
        default=False,
        verbose_name="Prioritario",
        help_text="Jumps ahead of the FIFO order on the waiting list. Set when a family should be offered "
        "the next free spot regardless of how long others have waited (a sibling, a returning student).",
    )
    withdrawal_date = models.DateField(null=True, blank=True)
    withdrawal_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "students"
        verbose_name = "Alumno"
        verbose_name_plural = "Alumnos"
        indexes = [
            # No `Index(fields=["group"])` — `group` is a ForeignKey and Django
            # indexes those by default. See the note on `Payment.Meta.indexes`.
            models.Index(fields=["active"]),
            models.Index(fields=["birth_date"]),
            models.Index(fields=["is_waiting"]),
        ]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        # `last_name` is blank for a waiting-list entry taken over the phone.
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def age(self):
        """Age in years, or None when no birth date is on record.

        `birth_date` is optional (waiting-list entries may only have a name and
        a phone number), so callers must handle None — templates render it as
        blank, which is what we want.
        """
        from datetime import date

        if not self.birth_date:
            return None
        today = date.today()
        return (
            today.year
            - self.birth_date.year
            - ((today.month, today.day) < (self.birth_date.month, self.birth_date.day))
        )

    def save(self, *args, **kwargs):
        if self.is_waiting and self.waiting_since is None:
            from django.utils import timezone

            self.waiting_since = timezone.now()
        elif not self.is_waiting and self.waiting_since is not None:
            self.waiting_since = None
        super().save(*args, **kwargs)


class StudentParent(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    parent = models.ForeignKey(Parent, on_delete=models.CASCADE)

    class Meta:
        db_table = "student_parents"
        verbose_name = "Vínculo alumno–familiar"
        verbose_name_plural = "Vínculos alumno–familiar"
        constraints = [
            models.UniqueConstraint(fields=["student", "parent"], name="unique_student_parent"),
        ]

    def __str__(self):
        return f"{self.parent} -> {self.student}"


@receiver(pre_save, sender=Student)
def _capture_active_transition(sender, instance, **kwargs):
    """Stash the DB value of `active` so post_save can detect True→False.

    Fetches the FULL row and publishes it as `instance._presave_db_obj`, which
    `core.audit_signals._capture_pre_save_snapshot` reuses. Both are `pre_save`
    receivers that needed the same row, and each was running its own
    `objects.get()` — two round trips per Student save for one row. This receiver
    is connected at model-import time and therefore fires first; if that order
    ever changes the audit receiver simply fetches for itself, so correctness
    does not depend on it.

    A deferred `.only("active")` instance cannot be shared: the audit snapshot
    reads a dozen fields off it and each one would trigger its own query.
    """
    if instance.pk is None:
        instance._active_was = None
        instance._presave_db_obj = None
        return
    try:
        current = Student.objects.get(pk=instance.pk)
    except Student.DoesNotExist:
        instance._active_was = None
        instance._presave_db_obj = None
        return
    instance._presave_db_obj = current
    instance._active_was = current.active


@receiver(post_save, sender=Student)
def _notify_group_spot_freed(sender, instance, created, **kwargs):
    """Log a HistoryLog entry when a student is deactivated and their group has waiters."""
    if created:
        return
    was_active = getattr(instance, "_active_was", None)
    if was_active is not True or instance.active:
        return
    # Late import to avoid circular imports at app-loading time.
    from core.views.waiting_list import notify_capacity_freed

    notify_capacity_freed(instance)


# Public model surface of this module.
__all__ = [
    "Group",
    "Parent",
    "Student",
    "StudentParent",
    "Teacher",
]
