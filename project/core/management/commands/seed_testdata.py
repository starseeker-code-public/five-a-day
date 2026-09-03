"""
Management command to populate the database with a rich, COHERENT QA dataset.

Designed for the QA / testing environment so testers can exercise every
feature (students, groups, enrollments, payments, reports, expenses, waiting
list, schedule) against realistic, internally-consistent data.

All monetary amounts are derived from the live ``SiteConfiguration`` via the
billing service layer (``EnrollmentService`` / ``PaymentService`` /
``PricingService``) — nothing is hard-coded, so the numbers always match what
the app itself would compute.

Usage:
    python manage.py seed_testdata          # Full seed (no-op if data exists)
    python manage.py seed_testdata --reset  # Wipe and re-seed
    python manage.py seed_testdata --small  # Minimal seed (fewer records)
"""

import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from billing.models import (
    Enrollment,
    EnrollmentType,
    Expense,
    Payment,
    SiteConfiguration,
    academic_year_end_date,
    academic_year_start_date,
    current_academic_year,
)
from billing.services.enrollment_service import EnrollmentService
from billing.services.enrollment_type_service import ensure_enrollment_types
from billing.services.payment_service import PaymentService
from core.models import FunFridayAttendance, HistoryLog, ScheduleSlot, TodoItem
from students.models import Group, Parent, Student, StudentParent, Teacher

# ---------------------------------------------------------------------------
# Seed data constants
# ---------------------------------------------------------------------------

# Two ADDITIONAL (non-admin) teachers created by this command. The two REAL
# admin teachers (seeded by `seed_teachers`) are looked up at runtime and never
# touched. Any teacher whose email ends with this domain is considered
# command-created and is safe to wipe on --reset.

ADDITIONAL_TEACHERS = [
    {
        "first_name": "Diego",
        "last_name": "Ramos",
        "email": "diego.ramos@fiveaday.test",
        "phone": "600100010",
        "admin": False,
    },
    {
        "first_name": "Nuria",
        "last_name": "Iglesias",
        "email": "nuria.iglesias@fiveaday.test",
        "phone": "600100011",
        "admin": False,
    },
]

# Enrollment types that must exist. Seeded via the real config defaults so the
# base amounts always mirror SiteConfiguration.

# Group name -> teacher slot. "admin1"/"admin2" resolve to the two existing
# admin teachers; "add1"/"add2" to the two additional teachers this command
# creates. MOST groups belong to the admin teachers; each additional teacher
# owns exactly one group.
GROUPS = [
    {"group_name": "Starters A", "color": "#6366f1", "teacher": "admin1", "max_students": 8},
    {"group_name": "Starters B", "color": "#8b5cf6", "teacher": "admin1", "max_students": 8},
    {"group_name": "Movers A", "color": "#06b6d4", "teacher": "admin1", "max_students": 8},
    {"group_name": "Movers B", "color": "#0ea5e9", "teacher": "admin2", "max_students": 8},
    {"group_name": "Flyers A", "color": "#10b981", "teacher": "admin2", "max_students": 8},
    {"group_name": "Flyers B", "color": "#22c55e", "teacher": "admin2", "max_students": 8},
    {"group_name": "Teens", "color": "#f59e0b", "teacher": "add1", "max_students": 10},
    {"group_name": "Adults", "color": "#ef4444", "teacher": "add2", "max_students": 12},
]

# 15 parents. Index 0 and 1 are the two "sibling" parents (each with 2 children);
# indices 2..14 are singletons. The inactive student reuses parent index 2.
# (first_name, last_name, dni, phone, email, iban)
PARENTS = [
    ("Isabel", "Garrido", "12345678A", "600200001", "isabel.garrido@test.com", "ES1234567890123456789001"),
    ("Ramon", "Ortega", "23456789B", "600200002", "ramon.ortega@test.com", "ES1234567890123456789002"),
    ("Cristina", "Ruiz", "34567890C", "600200003", "cristina.ruiz@test.com", "ES1234567890123456789003"),
    ("Antonio", "Gil", "45678901D", "600200004", "antonio.gil@test.com", "ES1234567890123456789004"),
    ("Lucia", "Marin", "56789012E", "600200005", "lucia.marin@test.com", "ES1234567890123456789005"),
    ("Sergio", "Soler", "67890123F", "600200006", "sergio.soler@test.com", "ES1234567890123456789006"),
    ("Beatriz", "Vidal", "78901234G", "600200007", "beatriz.vidal@test.com", "ES1234567890123456789007"),
    ("Javier", "Bravo", "89012345H", "600200008", "javier.bravo@test.com", "ES1234567890123456789008"),
    ("Patricia", "Campos", "90123456J", "600200009", "patricia.campos@test.com", "ES1234567890123456789009"),
    ("Fernando", "Ferrer", "01234567K", "600200010", "fernando.ferrer@test.com", "ES1234567890123456789010"),
    ("Marta", "Molina", "11234567L", "600200011", "marta.molina@test.com", "ES1234567890123456789011"),
    ("Alberto", "Reyes", "21234567M", "600200012", "alberto.reyes@test.com", "ES1234567890123456789012"),
    ("Silvia", "Castro", "31234567N", "600200013", "silvia.castro@test.com", "ES1234567890123456789013"),
    ("Raul", "Prieto", "41234567P", "600200014", "raul.prieto@test.com", "ES1234567890123456789014"),
    ("Nuria", "Leon", "51234567Q", "600200015", "nuria.leon@test.com", "ES1234567890123456789015"),
]

# 17 CHILDREN. Fields:
#   first, last, birth_date, gender, group, school, parent_index,
#   plan ('monthly_full'|'monthly_part'|'quarterly'),
#   sibling (bool), language_cheque (bool), returning (bool)
# Exactly 4 quarterly (indices 6, 8, 12, 15); the rest monthly.
CHILDREN = [
    (
        "Lucia",
        "Garrido",
        date(2016, 3, 15),
        "f",
        "Starters A",
        "CEIP Parque Sur",
        0,
        "monthly_full",
        True,
        False,
        False,
    ),
    (
        "Pablo",
        "Garrido",
        date(2018, 7, 22),
        "m",
        "Starters B",
        "CEIP Parque Sur",
        0,
        "monthly_part",
        True,
        False,
        False,
    ),
    (
        "Sofia",
        "Ortega",
        date(2015, 11, 8),
        "f",
        "Movers A",
        "CEIP Cristobal Valera",
        1,
        "monthly_full",
        True,
        False,
        False,
    ),
    (
        "Mateo",
        "Ortega",
        date(2017, 1, 30),
        "m",
        "Movers B",
        "CEIP Cristobal Valera",
        1,
        "monthly_full",
        True,
        False,
        False,
    ),
    (
        "Daniel",
        "Ruiz",
        date(2016, 6, 12),
        "m",
        "Starters A",
        "Colegio Compania de Maria",
        2,
        "monthly_full",
        False,
        False,
        True,
    ),
    ("Martina", "Gil", date(2014, 5, 12), "f", "Flyers A", "CEIP Villacerrada", 3, "monthly_part", False, False, False),
    ("Hugo", "Marin", date(2015, 9, 3), "m", "Movers A", "CEIP Benjamin Palencia", 4, "quarterly", False, False, False),
    (
        "Valeria",
        "Soler",
        date(2017, 4, 18),
        "f",
        "Starters B",
        "Colegio Sabina Mora",
        5,
        "monthly_full",
        False,
        True,
        False,
    ),
    ("Alejandro", "Vidal", date(2014, 12, 1), "m", "Flyers B", "CEIP Parque Sur", 6, "quarterly", False, False, False),
    ("Emma", "Bravo", date(2013, 8, 25), "f", "Teens", "IES Bachiller Sabuco", 7, "monthly_full", False, False, True),
    (
        "Leo",
        "Campos",
        date(2018, 2, 14),
        "m",
        "Starters A",
        "CEIP Cristobal Valera",
        8,
        "monthly_part",
        False,
        False,
        False,
    ),
    (
        "Alba",
        "Ferrer",
        date(2015, 10, 7),
        "f",
        "Flyers A",
        "Colegio Compania de Maria",
        9,
        "monthly_full",
        False,
        True,
        False,
    ),
    ("Adrian", "Molina", date(2016, 6, 20), "m", "Movers B", "CEIP Villacerrada", 10, "quarterly", False, False, False),
    ("Noa", "Reyes", date(2013, 11, 30), "f", "Teens", "IES Bachiller Sabuco", 11, "monthly_full", False, False, False),
    (
        "Bruno",
        "Castro",
        date(2016, 1, 9),
        "m",
        "Flyers B",
        "CEIP Benjamin Palencia",
        12,
        "monthly_full",
        False,
        False,
        True,
    ),
    ("Julia", "Prieto", date(2015, 4, 3), "f", "Movers A", "Colegio Sabina Mora", 13, "quarterly", False, False, False),
    (
        "Marcos",
        "Leon",
        date(2017, 7, 27),
        "m",
        "Starters B",
        "CEIP Parque Sur",
        14,
        "monthly_full",
        False,
        False,
        False,
    ),
]

# 3 ADULTS — no parent, own email/phone. (first, last, birth_date, gender, email, phone)
ADULTS = [
    ("Isabel", "Torres", date(1985, 3, 10), "f", "isabel.torres@test.com", "600300001"),
    ("Miguel", "Serrano", date(1990, 8, 22), "m", "miguel.serrano@test.com", "600300002"),
    ("Rosa", "Diaz", date(1978, 12, 5), "f", "rosa.diaz@test.com", "600300003"),
]


class Command(BaseCommand):
    help = "Populate the database with a rich, coherent test dataset for QA."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing seed data before seeding (keeps the seeded admin teachers).",
        )
        parser.add_argument(
            "--small",
            action="store_true",
            help="Create a smaller dataset (fewer students and payments).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        # Refuse --reset outright in production. The HTTP route into this
        # command (`api_seed_database`) is already gated on IS_TESTING_ENV plus
        # an admin Teacher, but the command itself would run anywhere — a
        # mis-targeted `gcloud run jobs execute` away from deleting every
        # student, parent, enrollment and payment the academy has. Every other
        # destructive surface in this project is environment-guarded; this was
        # the one that was not.
        if options["reset"] and getattr(settings, "ENVIRONMENT", "development") == "production":
            raise CommandError(
                "seed_testdata --reset borra TODOS los alumnos, padres, matriculas y pagos "
                "y esta bloqueado en produccion. Si de verdad es lo que quieres, cambia "
                "ENVIRONMENT primero — no existe un override por diseno."
            )

        if options["reset"]:
            self._reset()

        if Student.objects.exists():
            self.stdout.write(self.style.WARNING("Database already has students. Use --reset to wipe and re-seed."))
            return

        self.small = options["small"]
        self.stdout.write("Seeding test data...")

        self.config = SiteConfiguration.get_config()
        self.stdout.write("  Site configuration ready.")
        self._seed_enrollment_types()

        self.acad_year = current_academic_year()
        self.start_year = int(self.acad_year.split("-")[0])
        self.end_year = int(self.acad_year.split("-")[1])
        self.sept_start = academic_year_start_date(self.start_year)

        # --small keeps a curated subset that still covers siblings, a quarterly
        # enrollment, a language cheque and a returning student.
        self.child_data = CHILDREN[:8] if self.small else CHILDREN

        teachers = self._seed_teachers()
        groups = self._seed_groups(teachers)
        parents = self._seed_parents()
        self._seed_children(groups, parents)
        self._seed_adults(groups)
        self._seed_inactive_student(groups, parents)
        self._assign_payment_statuses()
        self._seed_expenses()
        self._seed_schedule(groups)
        self._seed_fun_friday()
        self._seed_todos()
        self._seed_history()

        self._print_summary()

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def _reset(self):
        self.stdout.write(self.style.WARNING("Deleting existing seed data..."))
        HistoryLog.objects.all().delete()
        TodoItem.objects.all().delete()
        ScheduleSlot.objects.all().delete()
        FunFridayAttendance.objects.all().delete()
        Expense.objects.all().delete()
        Payment.objects.all().delete()
        Enrollment.objects.all().delete()
        StudentParent.objects.all().delete()
        Student.objects.all().delete()
        Parent.objects.all().delete()
        Group.objects.all().delete()
        # Only delete teachers this command created — NEVER the seeded admins.
        #
        # Matched on the exact addresses in ADDITIONAL_TEACHERS, not on the
        # `@fiveaday.test` domain. The domain is shared: the QA VM gives each
        # admin a second NON-ADMIN account at `@fiveaday.test` (seeds #4-#5, the
        # `claudia` / `silvia` handles) and `seed_demo_parents` owns
        # `demo.teacher@fiveaday.test`. An `email__endswith` filter therefore
        # deleted teachers this command never created, which is exactly what the
        # line above says must not happen.
        #
        # The linked `auth.User` has to go with them. `Teacher.user` is a
        # OneToOneField pointing AT auth.User, so deleting the Teacher does not
        # touch it, and `ensure_user()` mirrors `Teacher.admin` onto is_staff /
        # is_superuser. An orphan therefore keeps working credentials and its
        # superuser flags with no Teacher row left to explain it — it can still
        # sign in to /admin/, and it accumulates on every QA reset.
        command_teachers = Teacher.objects.filter(email__in=[spec["email"] for spec in ADDITIONAL_TEACHERS])
        orphan_user_ids = [uid for uid in command_teachers.values_list("user_id", flat=True) if uid is not None]
        deleted, _ = command_teachers.delete()
        users_deleted = 0
        if orphan_user_ids:
            users_deleted, _ = User.objects.filter(id__in=orphan_user_ids).delete()
        # EnrollmentType + SiteConfiguration are intentionally preserved.
        self.stdout.write(
            f"  Data deleted ({deleted} command-created teacher rows, {users_deleted} linked login account(s) removed)."
        )

    # ------------------------------------------------------------------
    # Reference data
    # ------------------------------------------------------------------

    def _seed_enrollment_types(self):
        # Shared with the seed_enrollment_types management command so QA and
        # testing/production can never disagree on which types exist. It also
        # creates `special`, which this command used to omit — a special student
        # could not be enrolled on a QA database.
        report = ensure_enrollment_types(self.config)
        total = sum(len(v) for v in report.values())
        self.stdout.write(f"  {total} enrollment types.")

    def _seed_teachers(self):
        """Return {'admin1','admin2','add1','add2'} -> Teacher."""
        admins = list(Teacher.objects.filter(admin=True).order_by("id")[:2])
        if len(admins) < 2:
            # CommandError, not RuntimeError: this is a missing precondition the
            # operator can fix, so it deserves a one-line message rather than a
            # traceback that reads like a crash.
            raise CommandError(
                "Expected at least 2 admin teachers (seeded by `seed_teachers`). "
                f"Found {len(admins)}. Run `seed_teachers` first."
            )

        result = {"admin1": admins[0], "admin2": admins[1]}
        for slot, spec in zip(("add1", "add2"), ADDITIONAL_TEACHERS, strict=True):
            obj, _ = Teacher.objects.get_or_create(email=spec["email"], defaults=spec)
            result[slot] = obj

        self.stdout.write(
            f"  Teachers: reusing admins {admins[0].full_name!r} + {admins[1].full_name!r}, "
            f"created {len(ADDITIONAL_TEACHERS)} additional."
        )
        return result

    def _seed_groups(self, teachers):
        result = {}
        for g in GROUPS:
            obj, _ = Group.objects.get_or_create(
                group_name=g["group_name"],
                defaults={
                    "color": g["color"],
                    "teacher": teachers[g["teacher"]],
                    "max_students": g["max_students"],
                },
            )
            result[g["group_name"]] = obj
        self.stdout.write(f"  {len(result)} groups.")
        return result

    def _seed_parents(self):
        # Only seed parents actually referenced by the students being created
        # (the child slice + the inactive student, which reuses parent index 2)
        # so --small never leaves childless parent rows.
        needed = {row[6] for row in self.child_data} | {2}
        result = {}
        for idx, (first, last, dni, phone, email, iban) in enumerate(PARENTS):
            if idx not in needed:
                continue
            obj, _ = Parent.objects.get_or_create(
                dni=dni,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "phone": phone,
                    "email": email,
                    "iban": iban,
                },
            )
            result[idx] = obj
        self.stdout.write(f"  {len(result)} parents.")
        return result

    # ------------------------------------------------------------------
    # Students + enrollments + payments (the coherent core)
    # ------------------------------------------------------------------

    def _seed_children(self, groups, parents):
        count = 0
        for first, last, bdate, gender, group_name, school, p_idx, plan, sibling, lc, returning in self.child_data:
            student = Student.objects.create(
                first_name=first,
                last_name=last,
                birth_date=bdate,
                gender=gender,
                group=groups[group_name],
                school=school,
                gdpr_signed=True,
            )
            StudentParent.objects.create(student=student, parent=parents[p_idx])
            self._enroll_and_bill(
                student,
                parent=parents[p_idx],
                is_adult=False,
                plan=plan,
                sibling=sibling,
                language_cheque=lc,
                returning=returning,
            )
            count += 1
        self.stdout.write(f"  {count} child students enrolled + billed.")

    def _seed_adults(self, groups):
        adult_group = groups["Adults"]
        data = ADULTS[:1] if self.small else ADULTS
        for first, last, bdate, gender, email, phone in data:
            student = Student.objects.create(
                first_name=first,
                last_name=last,
                birth_date=bdate,
                gender=gender,
                is_adult=True,
                email=email,
                phone=phone,
                group=adult_group,
                gdpr_signed=True,
            )
            self._enroll_and_bill(student, parent=None, is_adult=True)
        self.stdout.write(f"  {len(data)} adult students enrolled + billed.")

    def _enroll_and_bill(
        self, student, *, parent, is_adult, plan="monthly_full", sibling=False, language_cheque=False, returning=False
    ):
        """Mirror StudentCreateView: enrollment via service, matrícula payment
        with the returning-student discount, and the full academic year of
        pending periodic payments via PaymentService."""
        if returning:
            self._create_prior_enrollment(student)

        enrollment = EnrollmentService.create_enrollment(
            student,
            {
                "enrollment_plan": plan,
                "has_language_cheque": language_cheque,
                "is_sibling_discount": sibling,
                "is_special": False,
                "manual_amount": None,
            },
            is_adult=is_adult,
        )

        # Backdate so the whole academic year of payments is scheduled (the
        # service defaults enrollment_date to today, which for a past academic
        # year would suppress every period). Amounts are unaffected.
        enrollment.enrollment_date = self.sept_start
        enrollment.save(update_fields=["enrollment_date"])

        # Matrícula (enrollment fee) — same helper the real view uses, including
        # `this_academic_year` so a start-dated enrollment does not misread as
        # prior history and wrongly earn the returning-student discount.
        fee, returning_discount = EnrollmentService.compute_enrollment_fee(
            self.config, student, is_adult=is_adult, this_academic_year=enrollment.academic_year
        )
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
            due_date=self.sept_start,
            payment_date=self.sept_start,
            concept=concept,
        )

        # Recurring fees for every period that has already started. All pending;
        # statuses are spread coherently in _assign_payment_statuses.
        PaymentService.schedule_academic_year_payments(enrollment, parent)

    def _create_prior_enrollment(self, student):
        """A finished enrollment in the previous academic year so the student
        is detected as 'returning' (drives the returning-student discount)."""
        prev_start = self.start_year - 1
        prev_year = f"{prev_start}-{prev_start + 1}"
        # This IS the student's first enrollment — it is what makes the current-year one
        # count as returning, so the prior year itself is a new-student matrícula.
        enrollment_type = EnrollmentType.objects.get(name="new_student")
        base = self.config.full_time_monthly_fee
        Enrollment.objects.create(
            student=student,
            enrollment_type=enrollment_type,
            enrollment_period_start=academic_year_start_date(prev_start),
            enrollment_period_end=academic_year_end_date(prev_start + 1),
            academic_year=prev_year,
            schedule_type="full_time",
            payment_modality="monthly",
            enrollment_amount=base,
            discount_percentage=Decimal("0.00"),
            final_amount=base,
            status="finished",
            enrollment_date=academic_year_start_date(prev_start),
        )

    def _seed_inactive_student(self, groups, parents):
        """One withdrawn student (sibling of Daniel, reuses parent index 2, so
        the active-child parent count stays at 15). Finished enrollment, a
        completed matrícula, no scheduled fees."""
        student = Student.objects.create(
            first_name="Marco",
            last_name="Ruiz",
            birth_date=date(2018, 9, 4),
            gender="m",
            group=groups["Starters A"],
            school="Colegio Compania de Maria",
            active=False,
            withdrawal_date=date.today() - timedelta(days=75),
            withdrawal_reason="La familia se mudó a otra ciudad",
            gdpr_signed=True,
        )
        StudentParent.objects.create(student=student, parent=parents[2])

        enrollment_type = EnrollmentType.objects.get(name="new_student")
        base = self.config.full_time_monthly_fee
        enrollment = Enrollment.objects.create(
            student=student,
            enrollment_type=enrollment_type,
            enrollment_period_start=self.sept_start,
            enrollment_period_end=academic_year_end_date(self.end_year),
            academic_year=self.acad_year,
            schedule_type="full_time",
            payment_modality="monthly",
            enrollment_amount=base,
            discount_percentage=Decimal("0.00"),
            final_amount=base,
            status="finished",
            enrollment_date=self.sept_start,
        )
        Payment.objects.create(
            student=student,
            parent=parents[2],
            enrollment=enrollment,
            payment_type="enrollment",
            payment_method="transfer",
            amount=self.config.children_enrollment_fee,
            payment_status="completed",
            due_date=self.sept_start,
            payment_date=self.sept_start,
            concept=f"Matrícula {self.acad_year} — {student.full_name}",
        )
        self.stdout.write("  1 inactive student (Marco Ruiz).")

    def _assign_payment_statuses(self):
        """Spread payment statuses coherently across the seeded year.

        Every payment_status is represented (>=2 each), and the report/expense
        pages get real income in both the current and previous month.
        """
        today = date.today()
        current_income_date = today.replace(day=min(5, today.day))  # current month income
        prev_month_end = today.replace(day=1) - timedelta(days=1)  # previous month income
        completed_cutoff = date(self.end_year, 5, 1)  # Sep..Apr paid on time

        # --- Matrícula: leave the first 3 pending, rest already completed. ---
        matricula_ids = list(
            Payment.objects.filter(payment_type="enrollment", payment_status="completed")
            .order_by("id")
            .values_list("id", flat=True)
        )
        Payment.objects.filter(id__in=matricula_ids[:3]).update(payment_status="pending", payment_date=None)

        # --- Periodic (monthly + quarterly) payments. ---
        june_counter = 0
        for p in Payment.objects.filter(payment_type__in=["monthly", "quarterly"]).order_by("student_id", "due_date"):
            due = p.due_date
            if p.payment_type == "quarterly":
                if due.month == 4:
                    # Q3 (Apr) — leave pending so there is >=1 pending quarterly (overdue).
                    p.payment_status = "pending"
                    p.payment_date = None
                else:
                    p.payment_status = "completed"
                    p.payment_date = due
            elif due.month == 6 and due.year == self.end_year:
                # June: split across pending / paid-in-July / paid-in-June.
                bucket = june_counter % 3
                june_counter += 1
                if bucket == 0:
                    p.payment_status = "pending"  # overdue last month
                    p.payment_date = None
                elif bucket == 1:
                    p.payment_status = "completed"  # current-month income
                    p.payment_date = current_income_date
                else:
                    p.payment_status = "completed"  # previous-month income
                    p.payment_date = prev_month_end
            elif due < completed_cutoff or due.month == 5:
                p.payment_status = "completed"
                p.payment_date = due
            else:
                p.payment_status = "pending"
                p.payment_date = None
            p.save(update_fields=["payment_status", "payment_date"])

        # --- Variety: failed / cancelled / refunded from October monthly fees. ---
        oct_ids = list(
            Payment.objects.filter(payment_type="monthly", payment_status="completed", due_date__month=10)
            .order_by("id")
            .values_list("id", flat=True)
        )
        Payment.objects.filter(id__in=oct_ids[0:3]).update(payment_status="failed", payment_date=None)
        Payment.objects.filter(id__in=oct_ids[3:6]).update(payment_status="cancelled", payment_date=None)
        # Refunded payments were collected then returned — keep their payment_date.
        Payment.objects.filter(id__in=oct_ids[6:9]).update(payment_status="refunded")

        self.stdout.write("  Payment statuses assigned (all states covered).")

    # ------------------------------------------------------------------
    # Expenses (v1.5) — small realistic academy costs, current + prev month
    # ------------------------------------------------------------------

    def _seed_expenses(self):
        today = date.today()
        prev_month_end = today.replace(day=1) - timedelta(days=1)
        cur_year, cur_month = today.year, today.month
        prev_year, prev_month = prev_month_end.year, prev_month_end.month

        # (day, category, description, amount)
        prev = [
            (4, "supplies", "Fotocopias y material impreso", Decimal("24.50")),
            (7, "supplies", "Rotuladores y material de pizarra", Decimal("18.90")),
            (12, "utilities", "Productos de limpieza", Decimal("32.00")),
            (18, "other", "Snacks para Fun Friday", Decimal("27.40")),
            (20, "software", "Suscripción Canva Pro", Decimal("11.99")),
            (25, "marketing", "Flyers para jornada de puertas abiertas", Decimal("45.00")),
        ]
        cur = [
            (2, "supplies", "Fotocopias y material impreso", Decimal("19.80")),
            (5, "supplies", "Libros de lectura para Movers", Decimal("62.00")),
            (7, "supplies", "Material de oficina (bolígrafos, carpetas)", Decimal("21.35")),
            (9, "other", "Snacks para Fun Friday", Decimal("24.90")),
            (10, "software", "Suscripción Canva Pro", Decimal("11.99")),
            (11, "other", "Café y suministros de cocina", Decimal("15.60")),
        ]
        if self.small:
            prev, cur = prev[:3], cur[:3]

        count = 0
        for day, cat, desc, amt in prev:
            Expense.objects.create(
                description=desc,
                category=cat,
                amount=amt,
                expense_date=date(prev_year, prev_month, min(day, prev_month_end.day)),
                is_recurring=False,
            )
            count += 1
        cur_last = calendar.monthrange(cur_year, cur_month)[1]
        for day, cat, desc, amt in cur:
            Expense.objects.create(
                description=desc,
                category=cat,
                amount=amt,
                expense_date=date(cur_year, cur_month, min(day, cur_last)),
                is_recurring=False,
            )
            count += 1
        self.stdout.write(f"  {count} expenses (current + previous month).")

    # ------------------------------------------------------------------
    # Nice-to-haves
    # ------------------------------------------------------------------

    def _seed_schedule(self, groups):
        group_list = [g for name, g in groups.items() if name not in ("Adults", "Teens")]
        if not group_list:
            return
        slots_created = 0
        for row in range(3):
            for day in range(5):  # Mon-Fri
                for col in range(2):
                    idx = (row * 10 + day * 2 + col) % len(group_list)
                    _, created = ScheduleSlot.objects.get_or_create(
                        row=row,
                        day=day,
                        col=col,
                        defaults={"group": group_list[idx]},
                    )
                    if created:
                        slots_created += 1
        self.stdout.write(f"  {slots_created} schedule slots.")

    def _seed_fun_friday(self):
        """A few Fun Friday attendance rows for the most recent Fridays."""
        students = list(Student.objects.filter(active=True, is_adult=False)[:6])
        if not students:
            return
        today = date.today()
        # Most recent Friday on or before today.
        last_friday = today - timedelta(days=(today.weekday() - 4) % 7)
        count = 0
        for wk in range(2):
            friday = last_friday - timedelta(weeks=wk)
            for student in students:
                _, created = FunFridayAttendance.objects.get_or_create(student=student, date=friday)
                if created:
                    count += 1
        self.stdout.write(f"  {count} Fun Friday attendance rows.")

    def _seed_todos(self):
        today = date.today()
        todos = [
            ("Revisar pagos pendientes de este mes", today + timedelta(days=3)),
            ("Enviar recordatorios de pago a las familias", today + timedelta(days=7)),
            ("Preparar actividades de Fun Friday", today + timedelta(days=5)),
            ("Actualizar datos de contacto de alumnos", today - timedelta(days=2)),  # overdue
        ]
        count = 0
        for text, due in todos:
            _, created = TodoItem.objects.get_or_create(text=text, defaults={"due_date": due})
            if created:
                count += 1
        self.stdout.write(f"  {count} todo items.")

    def _seed_history(self):
        entries = [
            ("student_enrolled", "Alumna matriculada: Lucia Garrido — Starters A"),
            ("payment_completed", "Pago recibido de Isabel Garrido"),
            ("email_sent", "Email de bienvenida enviado a isabel.garrido@test.com"),
            ("config_updated", "Configuración del sitio actualizada"),
            ("expense_added", "Gasto registrado: material de oficina"),
        ]
        for action, msg in entries:
            HistoryLog.log(action, msg)
        self.stdout.write(f"  {len(entries)} history entries.")

    # ------------------------------------------------------------------

    def _print_summary(self):
        from django.db.models import Count

        by_status = {
            row["payment_status"]: row["n"]
            for row in Payment.objects.values("payment_status").order_by().annotate(n=Count("id"))
        }
        self.stdout.write(
            self.style.SUCCESS(
                "Done! "
                f"students={Student.objects.count()} "
                f"(active={Student.objects.filter(active=True).count()}), "
                f"parents={Parent.objects.count()}, "
                f"teachers={Teacher.objects.count()}, "
                f"groups={Group.objects.count()}, "
                f"enrollments={Enrollment.objects.count()}, "
                f"payments={Payment.objects.count()}, "
                f"expenses={Expense.objects.count()}"
            )
        )
        self.stdout.write(f"  Payments by status: {by_status}")
