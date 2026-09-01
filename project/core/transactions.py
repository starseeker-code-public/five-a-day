from django.db.models import Prefetch

from billing.models import Enrollment, Payment
from students.models import Student


def get_active_students():
    """Return a queryset of active students with related data pre-fetched."""
    return (
        Student.objects.filter(active=True)
        .select_related("group", "group__teacher")
        .prefetch_related(
            "parents",
            Prefetch(
                "enrollments",
                queryset=Enrollment.objects.select_related("enrollment_type"),
            ),
        )
    )


def get_all_payments_unrestricted():
    """Return all payments without date restrictions, with related data."""
    return (
        Payment.objects.select_related(
            "student",
            "parent",
            "enrollment",
            "enrollment__enrollment_type",
        )
        .prefetch_related(
            "student__parents",
            "student__enrollments",
            Prefetch("student__group__teacher"),
        )
        .order_by("-created_at", "-id")  # tie-break — see helper above
    )
