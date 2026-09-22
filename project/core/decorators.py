"""
Core decorators — reusable access-control decorators.
"""

from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.urls import Resolver404, resolve

from core.middleware import (
    TESTER_ALLOWED_URL_NAMES,
    TESTER_BLOCKED_MESSAGE,
    _is_non_admin_teacher,
    _is_tester_teacher,
)


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

        session = getattr(request, "session", None)
        authenticated = bool(session is not None and session.get("is_authenticated"))
        if not authenticated or (_is_non_admin_teacher(request) and not _tester_may_reach(request)):
            # Built from the setting, exactly as SimpleAuthMiddleware builds
            # its API_URL_PREFIX: the two layers must answer a blocked caller
            # identically, and a hand-typed second copy of the mount point is
            # how they would drift apart.
            if request.path.startswith(f"{settings.APP_PATH_PREFIX}/api/"):
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


def _tester_may_reach(request) -> bool:
    """True when a TESTER session is allowed at the URL it is currently asking for.

    `admin_required` guards 48 endpoints and the tester is `admin=False`, so
    without this the decorator refuses the role at every single one of them and
    `TESTER_ALLOWED_URL_NAMES` could never widen anything — the middleware would
    wave the request through and the view would turn it away one layer later.

    Note what this does NOT do: it does not give the decorator its own opinion
    about what a tester may reach. It asks the SAME frozenset the middleware
    asks, so the two layers cannot drift into disagreeing — which is the entire
    reason `admin_required` exists alongside the middleware in the first place.
    The decorator keeps its independent value: it still refuses an unauthenticated
    session and a plain non-admin teacher on its own authority, and a tester
    reaching a view whose URL name is absent from the set is refused here even if
    somebody later adds a second URL pointing at it.

    Falls back to the RESOLVED url_name rather than the path, matching the
    middleware. An unresolvable request answers False, i.e. refuses — the safe
    direction for a widening predicate.
    """
    if not _is_tester_teacher(request):
        return False
    match = getattr(request, "resolver_match", None)
    url_name = match.url_name if match is not None else None
    if url_name is None:
        try:
            url_name = resolve(request.path).url_name
        except Resolver404:
            return False
    return url_name in TESTER_ALLOWED_URL_NAMES


def tester_forbidden(view_func):
    """Refuse the public TESTER account, AT THE VIEW.

    Only needed on endpoints the tester would OTHERWISE reach, which is a much
    shorter list than it sounds: `TESTER_ALLOWED_URL_NAMES` is an allowlist, so
    everything absent from it is already refused by the middleware, and every
    `@admin_required` view additionally asks `_tester_may_reach`.

    That leaves exactly the endpoints inherited from
    `NON_ADMIN_ALLOWED_URL_NAMES` and then deliberately removed — `change_password`
    and `submit_support_ticket`. Those carry no `@admin_required` (an ordinary
    teacher is meant to use them), so for those two the middleware allowlist is
    the ONLY control, and a single-layer control on a public credential is the
    thing this codebase keeps learning not to ship. Hence a mirror, stating the
    rule where the dangerous code lives and surviving a URL rename.

    Response shape mirrors the middleware exactly — see the layer 2 comment there
    for why the wording is friendlier than `admin_required`'s.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if _is_tester_teacher(request):
            if request.path.startswith(f"{settings.APP_PATH_PREFIX}/api/"):
                return JsonResponse({"success": False, "error": TESTER_BLOCKED_MESSAGE}, status=403)
            messages.warning(request, f"🔒 {TESTER_BLOCKED_MESSAGE}")
            return redirect("home")
        return view_func(request, *args, **kwargs)

    return wrapper


def may_use_qa_tools(teacher):
    """THE predicate for the QA testing tools: testing env + admin + ACTIVE Teacher.

    Shared by `qa_access_required` (the enforcing gate) and
    `context_processors.show_testing_tools` (the icon's visibility) so the two
    can never disagree — they used to: the visibility check required `active`
    while the gate did not, leaving the enforcement weaker than the cosmetics.
    `active` matters as much as `admin`: deactivating a Teacher is how this
    academy offboards somebody, and the dev tools include DB seed/reset.
    """
    return bool(
        settings.IS_TESTING_ENV
        and teacher is not None
        and teacher.admin
        and teacher.active
        # BELT AND BRACES for the tester, which is `admin=False` and therefore
        # already excluded by the clause above. Stated anyway because these are
        # the DEV tools — database reset and re-seed, the error-email toggle, git
        # internals, and the button whose `repository_dispatch` ARMS a production
        # deploy — and the account's password is published on a portfolio site.
        # Saying it here rather than in `TESTER_ALLOWED_URL_NAMES` also covers all
        # twelve QA endpoints at once AND hides the icon, because this predicate
        # is the single thing `qa_access_required` and `show_testing_tools` read.
        and not teacher.tester
    )


def qa_access_required(view_func):
    """Block access unless DJANGO_ENV=testing (DEBUG=False) AND the request is
    made by a logged-in, ACTIVE, ADMIN Teacher. Returns 404 for everyone else
    so the page appears not to exist.

    The QA testing dashboard is gated on admin Teacher accounts (non-admin
    teachers must not see the dev tools: DB seed/reset, error-email toggle,
    git internals).
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not may_use_qa_tools(_request_teacher(request)):
            raise Http404
        return view_func(request, *args, **kwargs)

    return wrapper
