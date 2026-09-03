"""End-to-end smoke test against a RUNNING stack (not pytest).

Drives the real request paths with django.test.Client over the LIVE database:

    POST /students/create/   → Student + StudentParent + Enrollment
                               + matrícula Payment + the first prorated period
    POST /payments/create/   → a manual Payment

It exists to catch what the unit/integration suite structurally cannot: a
broken URL conf, missing ``EnrollmentType`` reference data, a mis-wired
form field name, a schedule that no longer issues the first period.

Run it with::

    make smoke
    # = docker compose exec -e PYTHONPATH=/app web \
    #     python project/manage.py shell -c \
    #     "from scripts.docker_smoke_test import run; run()"

LOCAL DEVELOPMENT ONLY. ``.dockerignore`` excludes ``scripts/``, so this file
is absent from every BUILT image — it is reachable inside the container only
through the ``.:/app`` mount in ``docker-compose.override.yml``, which the QA
VM deliberately does not load. It also WRITES real rows into whatever database
the stack is pointed at, which is a second reason not to run it on the VM.

History worth keeping: for three commits line 1 read
``from billing.models import ... Student``, and ``billing.models`` has never
defined or re-exported ``Student`` (billing refers to it as the string FK
``"students.Student"``). The script therefore raised ``ImportError`` on import
and had never once executed a single assertion, while its own comment claimed
the import had been fixed. Nothing in the repo invoked it either, so nothing
noticed. Hence ``make smoke`` and the identity checks below.
"""

from datetime import date, datetime

from django.conf import settings
from django.test import Client

# Student is a students.models model. billing.models holds the money models and
# the academic-year helpers; it references Student only as a string FK.
from billing.models import Enrollment, EnrollmentType, Payment, academic_year_for_month
from billing.services.enrollment_type_service import ensure_enrollment_types
from students.models import Group, Parent, Student, Teacher

# Every row this run creates carries the same tag. Without it the final lookup
# was `filter(first_name="Alumno", last_name="Smoke").order_by("-id").first()`,
# which happily returns a PREVIOUS run's student — so a POST that silently
# failed would still have found a row and reported SMOKE_OK.
TAG = datetime.now().strftime("%Y%m%d%H%M%S")
SMOKE_LAST_NAME = f"Smoke{TAG}"
SMOKE_CONCEPT = f"Pago smoke test {TAG}"


def _host() -> str:
    """A host ALLOWED_HOSTS will accept ('localhost' unless the env narrows it)."""
    allowed = [h for h in getattr(settings, "ALLOWED_HOSTS", []) if h and h != "*"]
    if not allowed or "localhost" in allowed:
        return "localhost"
    return allowed[0]


def _dump(response, label: str) -> None:
    """Print enough to diagnose a failed POST without dumping a whole page."""
    print(f"{label}_status={response.status_code}")
    location = response.headers.get("Location")
    if location:
        print(f"{label}_location={location}")
    if response.status_code == 200:
        # 200 on these views means the form was re-rendered, i.e. it FAILED.
        body = response.content.decode(response.charset or "utf-8", "replace")
        for marker in ("errorlist", "alert", "error"):
            idx = body.find(marker)
            if idx != -1:
                print(f"{label}_body[…{marker}…]={body[max(0, idx - 200) : idx + 400]}")
                break
        else:
            print(f"{label}_body_head={body[:600]}")


def run():
    print(f"smoke run tag={TAG} course={academic_year_for_month()}")

    # Reference data, not test data: _resolve_enrollment_type() resolves four
    # rows BY NAME and raises ValueError when one is missing — which the create
    # view swallows into a generic 200, so without this the failure reads as a
    # form problem. entrypoint.sh seeds them on every boot; this is idempotent.
    ensure_enrollment_types()
    types = sorted(EnrollmentType.objects.values_list("name", flat=True))
    print(f"enrollment_types={types}")

    teacher, _ = Teacher.objects.get_or_create(
        email="smoke.teacher@fiveaday.test",
        defaults={
            "first_name": "Smoke",
            "last_name": "Teacher",
            "phone": "600000001",
            "active": True,
        },
    )

    # max_students=0 means NO CAP. StudentForm.clean_group enforces the cap on
    # this exact write path, and Group.max_students defaults to 8 — so with the
    # default the 9th smoke run started failing validation with a 200 and no
    # explanation. `defaults` only apply on creation, hence the fix-up below.
    group, _ = Group.objects.get_or_create(
        group_name="Smoke Group",
        defaults={"teacher": teacher, "active": True, "max_students": 0},
    )
    if group.teacher_id != teacher.id or not group.active or group.max_students != 0:
        group.teacher = teacher
        group.active = True
        group.max_students = 0
        group.save(update_fields=["teacher", "active", "max_students"])

    # Created through the ORM on purpose: the portal invitation is sent by
    # ParentCreateView, not by a signal, so this writes no email.
    parent, _ = Parent.objects.get_or_create(
        dni="SMOKE1234A",
        defaults={
            "first_name": "Padre",
            "last_name": "Prueba",
            "phone": "600000002",
            "email": "parent.smoke@fiveaday.test",
            "iban": "",
        },
    )

    # SimpleAuthMiddleware authenticates on this session key alone. No
    # django.contrib.auth login, so request.user stays anonymous and the
    # non-admin teacher whitelist does not apply — this client is effectively
    # an admin, which is what the two views under test require.
    client = Client(HTTP_HOST=_host())
    session = client.session
    session["is_authenticated"] = True
    session["username"] = "smoke"
    session.save()

    # Only real field names. `enrollment_type`, `academic_year`,
    # `schedule_type`, `discount_percentage`, `status` and `notes` used to be
    # posted here and are not fields of either form — silently ignored, and
    # they made the script look like it was pinning behaviour it was not.
    # The enrollment start field is `start_date` (v1.26.8), NOT
    # `enrollment_date`: it becomes Enrollment.enrollment_date and drives the
    # academic year, the billing periods and the proration.
    student_post_data = {
        "first_name": "Alumno",
        "last_name": SMOKE_LAST_NAME,
        "birth_date": "15/02/2015",
        "school": "Colegio Smoke",
        "allergies": "",
        "gdpr_signed": "on",
        "group": str(group.id),
        # Read straight from POST by StudentCreateView.form_valid, not a form field.
        "parent_id": str(parent.id),
        # monthly_full | monthly_part | quarterly
        "enrollment_plan": "monthly_full",
        "start_date": date.today().strftime("%d/%m/%Y"),
    }

    student_response = client.post("/students/create/", data=student_post_data, secure=True)
    # Success redirects; a re-rendered 200 is a validation failure.
    if student_response.status_code != 302:
        _dump(student_response, "student_response")
        raise AssertionError(f"POST /students/create/ returned {student_response.status_code}, expected 302")

    student = Student.objects.filter(first_name="Alumno", last_name=SMOKE_LAST_NAME).order_by("-id").first()
    if not student:
        _dump(student_response, "student_response")
        raise AssertionError("No se creó el estudiante en base de datos")

    if not student.parents.filter(id=parent.id).exists():
        raise AssertionError("El estudiante no quedó vinculado al padre")

    enrollment = Enrollment.objects.filter(student=student).order_by("-id").first()
    if not enrollment:
        raise AssertionError("No se creó la matrícula del estudiante")

    # The schedule is the part most likely to rot silently: creating a student
    # must also issue the one-time matrícula and the FIRST period (prorated by
    # join date). Amounts are printed, never asserted — a literal here would be
    # a date bomb, since the first period is reduced on any day but the 1st.
    scheduled = list(
        Payment.objects.filter(student=student)
        .order_by("due_date", "id")
        .values_list("payment_type", "due_date", "amount")
    )
    print(f"scheduled_payments={scheduled}")
    kinds = {row[0] for row in scheduled}
    if "enrollment" not in kinds:
        raise AssertionError(f"No se creó el pago de matrícula (tipos creados: {sorted(kinds)})")
    if "monthly" not in kinds:
        raise AssertionError(f"No se creó el primer periodo mensual (tipos creados: {sorted(kinds)})")

    # payment_type="other" on purpose. `monthly`/`quarterly` are covered by
    # payments.unique_pending_periodic_payment_per_month, and the student above
    # already holds a PENDING monthly row due this month — so a `monthly` smoke
    # payment dated inside the current month is guaranteed to be refused, and
    # any fixed date is a calendar time bomb. `other` is exempt whatever the date.
    payment_post_data = {
        "student_id": str(student.id),
        "parent_id": str(parent.id),
        "payment_type": "other",
        "payment_method": "transfer",
        "amount": "36.00",
        "currency": "EUR",
        "payment_status": "pending",
        "due_date": date.today().strftime("%d/%m/%Y"),
        "payment_date": "",
        "concept": SMOKE_CONCEPT,
        "reference_number": f"SMOKE-{TAG}",
        "observations": "",
    }

    # create_payment redirects to the payments list on success AND on every
    # error it handles (the flash message carries the reason), so the status
    # code proves nothing here — only the row does.
    payment_response = client.post("/payments/create/", data=payment_post_data, secure=True)

    payment = Payment.objects.filter(student=student, parent=parent, concept=SMOKE_CONCEPT).order_by("-id").first()
    if not payment:
        _dump(payment_response, "payment_response")
        raise AssertionError("No se creó el pago en base de datos")

    print("SMOKE_OK")
    print(f"student_id={student.id}; name={student.full_name}")
    print(
        f"enrollment_id={enrollment.id}; academic_year={enrollment.academic_year}; start={enrollment.enrollment_date}"
    )
    print(f"payment_id={payment.id}; due_date={payment.due_date.strftime('%d/%m/%Y')}; amount={payment.amount}")
    print(f"student_response_status={student_response.status_code}")
    print(f"payment_response_status={payment_response.status_code}")


if __name__ == "__main__":
    run()
