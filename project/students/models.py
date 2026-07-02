from django.conf import settings
from django.core.validators import EmailValidator
from django.db import models
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver


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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "teachers"
        indexes = [
            models.Index(fields=["email"]),
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
            },
        )
        if not created:
            # Sync fields on the existing user record.
            user.email = self.email
            user.first_name = self.first_name
            user.last_name = self.last_name
            user.is_staff = self.admin
            user.is_superuser = self.admin

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


class Group(models.Model):
    group_name = models.CharField(max_length=100, unique=True)
    color = models.CharField(max_length=7, default="#6366f1")
    teacher = models.ForeignKey(Teacher, on_delete=models.PROTECT, related_name="groups")
    max_students = models.PositiveIntegerField(
        default=0,
        verbose_name="Cupo máximo",
        help_text="Soft limit on active enrolled students. 0 means no cap.",
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "groups"
        indexes = [
            models.Index(fields=["group_name"]),
            models.Index(fields=["teacher"]),
        ]

    def __str__(self):
        return self.group_name

    @property
    def enrolled_count(self):
        """Active, non-waiting students currently occupying a spot in this group."""
        return self.students.filter(active=True, is_waiting=False).count()

    @property
    def waiting_count(self):
        """Students on the waiting list whose preferred group is this one."""
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
        return bool(self.max_students) and self.enrolled_count >= self.max_students


class Parent(models.Model):
    last_name = models.CharField(max_length=100)
    first_name = models.CharField(max_length=100)
    dni = models.CharField(max_length=20, unique=True)
    phone = models.CharField(max_length=20)
    email = models.EmailField()
    iban = models.CharField(max_length=34, blank=True)  # International Bank Account Number
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)  # Added for consistency

    class Meta:
        db_table = "parents"
        indexes = [
            models.Index(fields=["dni"]),
            models.Index(fields=["email"]),
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.dni})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class Student(models.Model):
    last_name = models.CharField(max_length=100)
    first_name = models.CharField(max_length=100)
    birth_date = models.DateField()
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
    group = models.ForeignKey(Group, on_delete=models.PROTECT, related_name="students")
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
    withdrawal_date = models.DateField(null=True, blank=True)
    withdrawal_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "students"
        indexes = [
            models.Index(fields=["group"]),
            models.Index(fields=["active"]),
            models.Index(fields=["birth_date"]),
            models.Index(fields=["is_waiting"]),
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def age(self):
        from datetime import date

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
        constraints = [
            models.UniqueConstraint(fields=["student", "parent"], name="unique_student_parent"),
        ]

    def __str__(self):
        return f"{self.parent} -> {self.student}"


@receiver(pre_save, sender=Student)
def _capture_active_transition(sender, instance, **kwargs):
    """Stash the DB value of `active` so post_save can detect True→False."""
    if instance.pk is None:
        instance._active_was = None
        return
    try:
        instance._active_was = Student.objects.only("active").get(pk=instance.pk).active
    except Student.DoesNotExist:
        instance._active_was = None


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
