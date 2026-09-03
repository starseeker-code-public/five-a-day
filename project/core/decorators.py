"""
Core decorators — reusable access-control decorators.
"""

from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect


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


def admin_required(view_func):
    """Refuse anything but a logged-in ADMIN session, AT THE VIEW.

    Until v1.27.1 `NON_ADMIN_ALLOWED_URL_NAMES` in `core.middleware` was the
    SOLE authorization control on every financial write endpoint in the app —
    creating and completing payments, editing the price list, the P&L. That list
    is a deny-by-omission allowlist, which is the right shape, but it means the
    check lives in a file nobody edits when they add a URL: forget the entry and
    you have *blocked* something (loud, obvious), while forgetting to keep an
    entry OUT of it grants a privilege silently, and nothing at the view itself
    says who may call it.

    So: state it locally as well. Two independent controls have to agree, and
    the role requirement is readable where the code that needs it lives.

    The response shape MIRRORS the middleware exactly (403 JSON under `/api/`,
    otherwise a flash message and a redirect to the dashboard) so a blocked
    caller cannot tell which of the two layers stopped them, and so the
    frontend's error handling does not need a second branch.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        # Imported lazily: core.middleware imports nothing from here, but this
        # keeps the module import-cycle-free regardless of load order.
        from core.middleware import _is_non_admin_teacher

        session = getattr(request, "session", None)
        authenticated = bool(session is not None and session.get("is_authenticated"))
        if not authenticated or _is_non_admin_teacher(request):
            if request.path.startswith("/api/"):
                return JsonResponse(
                    {"success": False, "error": "No tienes permiso para esta acción."},
                    status=403,
                )
            if not authenticated:
                return redirect("login")
            messages.error(request, "❌ No tienes permiso para acceder a esa sección.")
            return redirect("home")
        return view_func(request, *args, **kwargs)

    return wrapper


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
