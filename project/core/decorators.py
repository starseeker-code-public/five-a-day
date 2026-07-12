"""
Core decorators — reusable access-control decorators.
"""

from functools import wraps

from django.conf import settings
from django.http import Http404


def _request_teacher(request):
    """Return the Teacher linked to the request's authenticated user, or None.

    Mirrors the reverse-OneToOne access used in core.middleware — the
    ``teacher`` accessor raises (subclasses AttributeError) when unlinked, so
    ``getattr(..., None)`` yields None for non-teacher / anonymous users.
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "teacher", None)


def qa_access_required(view_func):
    """Block access unless DJANGO_ENV=testing (DEBUG=False) AND the request is
    made by a logged-in ADMIN Teacher. Returns 404 for everyone else so the
    page appears not to exist.

    The QA testing dashboard is gated on admin Teacher accounts (non-admin
    teachers must not see the dev tools: DB seed/reset, error-email toggle,
    git internals).
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        teacher = _request_teacher(request)
        if not (settings.IS_TESTING_ENV and teacher is not None and teacher.admin):
            raise Http404
        return view_func(request, *args, **kwargs)

    return wrapper
