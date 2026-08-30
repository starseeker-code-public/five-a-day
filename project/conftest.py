from datetime import date
from decimal import Decimal

import pytest

from billing.models import Enrollment, EnrollmentType, Payment, SiteConfiguration
from students.models import Group, Parent, Student, StudentParent, Teacher


@pytest.fixture
def site_config(db):
    """Create or get the singleton SiteConfiguration."""
    return SiteConfiguration.get_config()


@pytest.fixture
def teacher(db):
    return Teacher.objects.create(
        first_name="Ana",
        last_name="García",
        email="ana@fiveaday.test",
        phone="600111222",
        active=True,
    )


@pytest.fixture
def group(db, teacher):
    return Group.objects.create(
        group_name="Group A",
        color="#8b5cf6",
        teacher=teacher,
        active=True,
    )


@pytest.fixture
def parent(db):
    return Parent.objects.create(
        first_name="María",
        last_name="López",
        dni="12345678A",
        phone="600333444",
        email="maria@test.com",
        iban="ES1234567890123456789012",
    )


@pytest.fixture
def second_parent(db):
    return Parent.objects.create(
        first_name="Pedro",
        last_name="Martín",
        dni="87654321B",
        phone="600555666",
        email="pedro@test.com",
    )


@pytest.fixture
def student(db, group):
    return Student.objects.create(
        first_name="Lucas",
        last_name="López García",
        birth_date=date(2018, 5, 15),
        school="CEIP Test",
        gdpr_signed=True,
        group=group,
        active=True,
    )


@pytest.fixture
def adult_student(db, group):
    return Student.objects.create(
        first_name="Carlos",
        last_name="Ruiz",
        birth_date=date(1990, 3, 10),
        is_adult=True,
        email="carlos@test.com",
        phone="600777888",
        gdpr_signed=True,
        group=group,
        active=True,
    )


@pytest.fixture
def student_with_parent(db, student, parent):
    StudentParent.objects.create(student=student, parent=parent)
    return student


# The four matrícula categories. `base_amount_*` is the one-time matrícula fee, so both
# columns carry the same figure — see billing/services/enrollment_type_service.py.
@pytest.fixture
def enrollment_type_new_student(db):
    et, _ = EnrollmentType.objects.get_or_create(
        name="new_student",
        defaults={
            "display_name": "Nuevo estudiante",
            "base_amount_full_time": Decimal("40.00"),
            "base_amount_part_time": Decimal("40.00"),
            "active": True,
        },
    )
    return et


@pytest.fixture
def enrollment_type_returning_student(db):
    et, _ = EnrollmentType.objects.get_or_create(
        name="returning_student",
        defaults={
            "display_name": "Antiguo estudiante",
            "base_amount_full_time": Decimal("20.00"),
            "base_amount_part_time": Decimal("20.00"),
            "active": True,
        },
    )
    return et


@pytest.fixture
def enrollment_type_adults(db):
    et, _ = EnrollmentType.objects.get_or_create(
        name="adults",
        defaults={
            "display_name": "Adulto",
            "base_amount_full_time": Decimal("20.00"),
            "base_amount_part_time": Decimal("20.00"),
            "active": True,
        },
    )
    return et


@pytest.fixture
def enrollment_type_special(db):
    et, _ = EnrollmentType.objects.get_or_create(
        name="special",
        defaults={
            "display_name": "Especial",
            "base_amount_full_time": Decimal("0.01"),
            "base_amount_part_time": Decimal("0.01"),
            "active": True,
        },
    )
    return et


@pytest.fixture
def active_enrollment(db, student, enrollment_type_new_student, site_config):
    return Enrollment.objects.create(
        student=student,
        enrollment_type=enrollment_type_new_student,
        enrollment_period_start=date(2025, 9, 15),
        enrollment_period_end=date(2026, 6, 27),
        academic_year="2025-2026",
        schedule_type="full_time",
        payment_modality="monthly",
        enrollment_amount=Decimal("54.00"),
        discount_percentage=Decimal("0.00"),
        final_amount=Decimal("54.00"),
        status="active",
        enrollment_date=date(2025, 9, 1),
    )


@pytest.fixture
def pending_payment(db, student, parent, active_enrollment):
    return Payment.objects.create(
        student=student,
        parent=parent,
        enrollment=active_enrollment,
        payment_type="monthly",
        payment_method="transfer",
        amount=Decimal("54.00"),
        payment_status="pending",
        due_date=date(2025, 10, 1),
        concept="Mensualidad Octubre 2025",
    )


@pytest.fixture
def completed_payment(db, student, parent, active_enrollment):
    return Payment.objects.create(
        student=student,
        parent=parent,
        enrollment=active_enrollment,
        payment_type="monthly",
        payment_method="transfer",
        amount=Decimal("54.00"),
        payment_status="completed",
        due_date=date(2025, 9, 1),
        payment_date=date(2025, 9, 5),
        concept="Mensualidad Septiembre 2025",
    )


@pytest.fixture
def inactive_student(db, group):
    return Student.objects.create(
        first_name="Withdrawn",
        last_name="Student",
        birth_date=date(2017, 1, 10),
        gdpr_signed=True,
        group=group,
        active=False,
        withdrawal_date=date(2026, 1, 15),
        withdrawal_reason="Moved to another city",
    )


@pytest.fixture
def cancelled_enrollment(db, student, enrollment_type_new_student, site_config):
    return Enrollment.objects.create(
        student=student,
        enrollment_type=enrollment_type_new_student,
        enrollment_period_start=date(2024, 9, 15),
        enrollment_period_end=date(2025, 6, 27),
        academic_year="2024-2025",
        schedule_type="full_time",
        payment_modality="monthly",
        enrollment_amount=Decimal("54.00"),
        discount_percentage=Decimal("0.00"),
        final_amount=Decimal("54.00"),
        status="cancelled",
        enrollment_date=date(2024, 9, 1),
    )


@pytest.fixture(autouse=True)
def _reset_dashboard_quote_cache():
    """Isolate the dashboard's module-level quote cache between tests.

    `core.views.dashboard` keeps the quote batch and its failure backoff in
    module globals (one per Gunicorn worker in production). Without this,
    whichever test ran first decided whether later ones saw a cache hit, a
    fetch, or a suppressed fetch during backoff.
    """
    from core.views.dashboard import reset_quote_cache

    reset_quote_cache()
    yield
    reset_quote_cache()


@pytest.fixture
def authenticated_client(client):
    """A Django test client with session-based auth (matching SimpleAuthMiddleware)."""
    session = client.session
    session["is_authenticated"] = True
    session["username"] = "testuser"
    session.save()
    return client
