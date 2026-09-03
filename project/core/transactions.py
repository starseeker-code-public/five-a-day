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
            # Was `Prefetch("student__group__teacher")`, which is a chain of
            # forward FKs: prefetching it costs two extra queries where a join
            # costs none.
            "student__group",
            "student__group__teacher",
        )
        # Deliberately NO `prefetch_related("student__parents", "student__enrollments")`.
        # `all_info` is this helper's only caller and the payments table in
        # `all_info.html` renders neither — it reads `payment.parent` (a direct
        # FK, joined above) and never touches the student's other parents or
        # enrollments. The two prefetches were therefore two extra queries per
        # page load fetching rows nothing displays. If a future caller does need
        # them, add them there rather than here, so the cost lands on the page
        # that asked for it.
        .order_by("-created_at", "-id")  # tie-break — see helper above
    )
