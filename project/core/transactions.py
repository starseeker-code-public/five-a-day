from django.db.models import Prefetch, Q

from billing.models import Enrollment, Payment, relevant_academic_years
from core.decorators import _request_teacher
from core.middleware import _is_non_admin_teacher, _is_tester_teacher
from students.models import Student


def students_on_the_roll(*, children_only=False):
    """Students actually studying THIS course — the roll, as every page means it.

    Active, not a waiting-list placeholder, and holding an enrollment in one of
    the relevant academic years (BOTH cohorts during the May–August overlap, or
    half the academy disappears from the list for four months of the year).

    One definition because there were three: the students list, the Fun Friday
    page and the re-enrolment candidate pool each spelled it out inline, so
    "who is on the roll" could be answered differently on three pages of the same
    app — and fixing it in one (a waiting-list entry leaking into Fun Friday) did
    not fix it in the others.

    `children_only` drops adult students, who have no guardian and do not take
    part in the children's activities.
    """
    queryset = Student.objects.filter(
        active=True,
        is_waiting=False,
        enrollments__academic_year__in=relevant_academic_years(),
    )
    if children_only:
        queryset = queryset.filter(is_adult=False)
    # `.distinct()` because the enrollment join multiplies a student by their
    # enrollments — two in the overlap window is normal, not a data problem.
    return queryset.distinct()


def visible_students_for(request, queryset=None):
    """Scope a `Student` queryset to the students the REQUESTING session may see.

    A non-admin teacher sees only **their own** students — the ones in the
    groups they teach (`Group.teacher`). Admins are unrestricted and get the
    queryset back untouched.

    THE one place that rule is written, because it has to hold on every surface
    that lists or opens a student: the roll (`StudentListView`), the ficha
    (`StudentDetailView` — where an unscoped view means a teacher can read any
    family's phone number and address by typing an id), and the autocomplete
    (`search_students`). Spelling it out per view is how the three would drift,
    and the drift is silent: it is invisible to admin testing, because admins
    take the early return.

    The TESTER account is unscoped like an admin, and has to be: it is
    `admin=False`, so it reaches this branch, but it teaches no groups — the
    filter below would match nothing and the demonstration would be an empty
    roll on every page. It is a shared public login over synthetic seed data, so
    there is no family whose privacy the scoping would be protecting.

    Two deliberate edges:

    * **Waiting-list placeholders stay visible.** A waiting entry is nobody's
      student yet — it is taken over the phone, usually with no group, and
      managing that queue IS in this role's whitelist
      (`waiting_list` / `waiting_list_create`). Scoping them out would let a
      teacher create an entry and then get a 404 opening the ficha they just
      made, from a link the waiting-list page itself renders.
    * **A restricted session with no Teacher row sees nothing.** That shape is a
      bare `auth.User` with no linked Teacher (`_is_non_admin_teacher` already
      restricts it by default-deny); "no groups" is the honest answer, and
      failing closed matches how every other control here behaves.
    """

    if queryset is None:
        queryset = students_on_the_roll()
    if not _is_non_admin_teacher(request) or _is_tester_teacher(request):
        return queryset
    teacher = _request_teacher(request)
    if teacher is None:
        return queryset.none()
    return queryset.filter(Q(group__teacher=teacher) | Q(is_waiting=True))


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
