"""
Idempotently seed a demo Parent (plus their children, enrollment and payments)
from DEMO_PARENT_<N>_* env vars, so the parent portal can actually be looked at.

Why this exists
---------------
The portal authenticates with a real email + password the family creates from a
single-use emailed link. That is right for real families and useless for "show
me what a parent sees": there is no mailbox to read in development, and on the
QA VM it meant fishing a link out of an inbox.

This command creates the DATA **and sets the portal password** from the env, so
the demo parent logs in through the ordinary /parent/login/ form — exactly the
same code path a real family uses. There is no separate demo login mode any
more; the previous one compared a plaintext env password inside the view, which
meant the thing QA exercised was not the thing production runs.

The credential lives in env vars (never in the repo, never in plaintext in the
database — it is stored hashed like any password), and the command refuses to
run in production, whose Cloud Run env has no DEMO_PARENT_* var anyway.

Env var contract (N starts at 1, iteration stops at the first missing USERNAME):
    DEMO_PARENT_<N>_USERNAME    required — identifies the block; also the
                                fallback for FIRST_NAME and DNI. NOT a login
                                handle: the portal logs in by email.
    DEMO_PARENT_<N>_PASSWORD    required — portal password, stored hashed
    DEMO_PARENT_<N>_EMAIL       required — the portal login id
    DEMO_PARENT_<N>_FIRST_NAME  optional, default derived from USERNAME
    DEMO_PARENT_<N>_LAST_NAME   optional, default "Demo"
    DEMO_PARENT_<N>_DNI         optional, default derived from USERNAME (unique)
    DEMO_PARENT_<N>_PHONE       optional, default "600000000"
    DEMO_PARENT_<N>_IBAN        optional
    DEMO_PARENT_<N>_CHILDREN    optional — comma-separated first names, e.g.
                                "Mateo,Valeria". Two or more names get the
                                sibling discount, which is the point: siblings
                                are the interesting case to look at.

Re-running is idempotent: the Parent is matched on EMAIL and each child on
(first_name, last_name, parent), and neither is duplicated when already present.
The password IS re-applied on every run — the env var is the source of truth for
a demo credential, and a QA VM where the documented password stopped working
because someone changed it in the UI is a support ticket, not a security win.
It never touches a Parent this command did not create.
"""

import os
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from billing.models import Payment, SiteConfiguration
from billing.services.enrollment_service import EnrollmentService
from billing.services.enrollment_type_service import ensure_enrollment_types
from billing.services.payment_service import PaymentService
from students.models import Group, Parent, Student, StudentParent, Teacher

# Group the demo children go into when the database has none yet (a fresh dev
# database). On the QA VM `seed_testdata` has already made real ones, and the
# first active group is reused instead.
DEMO_GROUP_NAME = "Demo"
DEMO_TEACHER_EMAIL = "demo.teacher@fiveaday.test"


class Command(BaseCommand):
    help = "Seed demo Parent + children + payments for the parent portal, from DEMO_PARENT_<N>_* env vars."

    def handle(self, *args, **options):
        # Demo credentials must never exist in production, and the portal's
        # password login refuses to run there anyway — so seeding the matching
        # data would only plant unreachable students in the academy's real roll.
        if getattr(settings, "ENVIRONMENT", "development") == "production":
            raise CommandError(
                "seed_demo_parents esta bloqueado en produccion — el portal de padres "
                "solo acepta el enlace magico alli. No hay override por diseno."
            )

        specs = list(iter_demo_parent_specs())
        if not specs:
            self.stdout.write(
                self.style.WARNING(
                    "No DEMO_PARENT_1_USERNAME found — nothing to seed. "
                    "Set DEMO_PARENT_<N>_* env vars in .env.development or .env.testing."
                )
            )
            return

        self.config = SiteConfiguration.get_config()
        ensure_enrollment_types()

        seeded = 0
        for spec in specs:
            with transaction.atomic():
                if self._seed_one(spec):
                    seeded += 1

        self.stdout.write(self.style.SUCCESS(f"Seeded {seeded} demo parent(s)."))

    # ------------------------------------------------------------------

    def _seed_one(self, spec) -> bool:
        parent = Parent.objects.filter(email__iexact=spec["email"]).first()
        if parent is None:
            parent = Parent.objects.create(
                first_name=spec["first_name"],
                last_name=spec["last_name"],
                dni=spec["dni"],
                phone=spec["phone"],
                email=spec["email"],
                iban=spec["iban"],
                # Stamped as already invited so the once-only invitation never
                # fires for a seeded parent: the password is set right below,
                # and an email about creating one would contradict it (and in
                # development go nowhere anyway).
                portal_invite_sent_at=timezone.now(),
            )
            self.stdout.write(self.style.SUCCESS(f"Demo parent created: {parent.full_name} <{parent.email}>"))
        else:
            self.stdout.write(f"Demo parent already exists: {parent.full_name} <{parent.email}>")

        # Re-applied every run so the documented demo password always works.
        parent.set_portal_password(spec["password"])
        if parent.portal_invite_sent_at is None:
            # Also stamped on the already-exists path, not just on creation: a
            # demo parent seeded before this guard existed would otherwise stay
            # a candidate for the "create your password" invitation, which
            # contradicts the password we just set for them.
            parent.portal_invite_sent_at = timezone.now()
            parent.save(update_fields=["portal_invite_sent_at", "updated_at"])
        self.stdout.write(f"  portal password set — log in at /parent/login/ with {parent.email}")

        names = spec["children"]
        if not names:
            self.stdout.write(f"  (no DEMO_PARENT_{spec['index']}_CHILDREN set — parent has no children)")
            return True

        group = self._resolve_group()
        # Two or more children under one parent IS the sibling case, and the
        # discount is what makes the portal's amounts worth looking at.
        sibling = len(names) > 1

        for offset, first_name in enumerate(names):
            student = Student.objects.filter(
                first_name=first_name,
                last_name=spec["last_name"],
                parents=parent,
            ).first()
            if student is not None:
                self.stdout.write(f"  child already exists: {student.full_name}")
                continue

            student = Student.objects.create(
                first_name=first_name,
                last_name=spec["last_name"],
                # Ages 8, 10, 12… derived from today, so the ficha never shows
                # an age that drifts as the years pass — a hard-coded birth date
                # is the date bomb this project has already been bitten by.
                birth_date=date(date.today().year - 8 - 2 * offset, 5, 12),
                gender="f" if offset % 2 else "m",
                group=group,
                school="CEIP Demo",
                gdpr_signed=True,
            )
            StudentParent.objects.create(student=student, parent=parent)
            self._enroll_and_bill(student, parent, sibling=sibling)
            self.stdout.write(self.style.SUCCESS(f"  child enrolled + billed: {student.full_name}"))

        return True

    def _resolve_group(self) -> Group:
        """Reuse a real group when the database has one; otherwise create a demo
        group (and the teacher it needs — `Group.teacher` is a non-null PROTECT
        FK, so there is no group without one)."""
        group = Group.objects.filter(active=True).order_by("id").first()
        if group is not None:
            return group

        teacher = Teacher.objects.order_by("id").first()
        if teacher is None:
            teacher, _created = Teacher.objects.get_or_create(
                email=DEMO_TEACHER_EMAIL,
                defaults={"first_name": "Demo", "last_name": "Teacher", "active": True, "admin": False},
            )
        group, _created = Group.objects.get_or_create(
            group_name=DEMO_GROUP_NAME,
            defaults={"color": "#8b5cf6", "teacher": teacher, "max_students": 0},
        )
        return group

    def _enroll_and_bill(self, student, parent, *, sibling: bool) -> None:
        """Mirror StudentCreateView: enrollment through the service, the
        matrícula payment, then every period that has already started."""
        enrollment = EnrollmentService.create_enrollment(
            student,
            {
                "enrollment_plan": "monthly_full",
                "has_language_cheque": False,
                "is_sibling_discount": sibling,
                "is_special": False,
                "manual_amount": None,
            },
            is_adult=False,
        )

        fee, returning_discount = EnrollmentService.compute_enrollment_fee(self.config, student, is_adult=False)
        concept = f"Matrícula {enrollment.academic_year} — {student.full_name}"
        if returning_discount:
            concept += f" (dto. alumno recurrente −{returning_discount:.2f} €)"
        Payment.objects.create(
            student=student,
            parent=parent,
            enrollment=enrollment,
            payment_type="enrollment",
            payment_method="transfer",
            amount=fee,
            currency="EUR",
            payment_status="completed",
            due_date=enrollment.enrollment_date,
            payment_date=enrollment.enrollment_date,
            concept=concept,
        )

        PaymentService.schedule_academic_year_payments(enrollment, parent)


def iter_demo_parent_specs():
    """
    Yield one normalised spec dict per DEMO_PARENT_<N>_* block.

    This command is the ONLY reader of the contract. Until v1.27 the portal
    view read it too, to verify a demo password inline; that second reader was
    also a second login code path, so QA signed off on a flow production never
    executed. The password now goes into the Parent row and the demo logs in
    through the ordinary form.
    """
    index = 1
    while True:
        prefix = f"DEMO_PARENT_{index}_"
        username = (os.getenv(f"{prefix}USERNAME") or "").strip()
        if not username:
            break  # Stop at the first gap.

        email = (os.getenv(f"{prefix}EMAIL") or "").strip()
        password = os.getenv(f"{prefix}PASSWORD") or ""
        if email and password:
            children = [n.strip() for n in (os.getenv(f"{prefix}CHILDREN") or "").split(",") if n.strip()]
            yield {
                "index": index,
                "username": username,
                "password": password,
                "first_name": (os.getenv(f"{prefix}FIRST_NAME") or username.capitalize()).strip(),
                "last_name": (os.getenv(f"{prefix}LAST_NAME") or "Demo").strip(),
                "email": email,
                "dni": (os.getenv(f"{prefix}DNI") or f"DEMO{username[:12].upper()}").strip(),
                "phone": (os.getenv(f"{prefix}PHONE") or "600000000").strip(),
                "iban": (os.getenv(f"{prefix}IBAN") or "").strip(),
                "children": children,
            }
        index += 1
