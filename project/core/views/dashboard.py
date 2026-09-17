import calendar as cal_module
import logging
import time
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, cast

import httpx
from django.core.paginator import Paginator
from django.db.models import Case, DecimalField, Sum, Value, When
from django.shortcuts import render

from billing.constants import LIVE_PAYMENT_STATUSES
from billing.models import Payment
from core.constants import SCHEDULED_APPS
from core.decorators import admin_required
from core.models import TodoItem
from core.transactions import get_active_students, get_all_payments_unrestricted
from core.views.waiting_list import group_capacity_summary
from students.models import Group, Student

# Dates shown per recurring scheduled email on the home card (Fun Friday repeats weekly).
MAX_EVENT_DATES = 2

logger = logging.getLogger(__name__)

_QUOTE_COOKIE = "last_quote"
_QUOTE_COOKIE_TTL = 7 * 24 * 60 * 60  # 7 days — long TTL, only a fallback
_QUOTE_API_URL = (
    "https://zenquotes.io/api/quotes"  # no trailing slash — the API 301-redirects "/api/quotes/" → "/api/quotes"
)
_QUOTE_BATCH_SIZE = 50
_QUOTE_FALLBACK = "¡Cada día es una nueva oportunidad para inspirar!"

# In-memory quote cache — list of (quote, author) tuples.  Each page load
# pops one.  When empty, _fetch_quotes() refills it with up to 50 quotes
# from zenquotes.io in a single HTTP call.  Per-worker (Gunicorn forks),
# so each process maintains its own list — acceptable given the API is free
# and the batch size amortises the cost.
# Thread safety: safe for sync Gunicorn workers (each process has its own
# list); not safe for async workers or multithreaded servers.
_quotes: list[tuple[str, str]] = []

# Monotonic timestamp until which we will NOT retry the quotes API. Without it,
# an outage meant every single dashboard load paid the full 5s timeout, because
# the cache stayed empty and the `if not _quotes` guard re-fetched every time.
_quotes_retry_after: float = 0.0
_QUOTE_FAILURE_BACKOFF = 10 * 60  # seconds


def reset_quote_cache() -> None:
    """Clear the in-memory quote cache and its failure backoff.

    Both are module-level (per Gunicorn worker), which makes them shared state
    across tests in the same process. Exposed so the test suite can reset them
    between cases rather than having one test's backoff silently suppress
    another's fetch.
    """
    global _quotes_retry_after

    _quotes.clear()
    _quotes_retry_after = 0.0


def _fetch_quotes() -> list[tuple[str, str]]:
    """Fetch up to 50 quotes from zenquotes.io.

    Returns a list of (quote_text, author) tuples.  The ``[AUTH]`` placeholder
    that zenquotes returns when rate-limited is filtered out.
    """
    try:
        resp = httpx.get(_QUOTE_API_URL, timeout=5.0, follow_redirects=True)
        resp.raise_for_status()
        items = resp.json()
        batch = [
            (item["q"], item["a"]) for item in items[:_QUOTE_BATCH_SIZE] if item.get("q") and item["q"] != "[AUTH]"
        ]
        if not batch:
            logger.warning(
                "zenquotes API returned no usable quotes (got %d items)",
                len(items) if isinstance(items, list) else 0,
            )
        return batch
    except Exception as e:  # noqa: BLE001 — a third-party HTTP call; the quote is decoration
        logger.warning("zenquotes API fetch failed: %s: %s", type(e).__name__, e)
        return []


def _get_quote(request):
    """
    Returns (quote_text, author_or_None, new_cookie_value_or_None).

    Pops one quote from the in-memory cache.  If empty, fetches a fresh batch
    of 50.  Falls back to the cookie (stores the last served quote as a plain
    ``"quote — author"`` string), then to a hardcoded Spanish string.

    Every page load sees a different quote — no day-based logic.
    """
    global _quotes_retry_after

    if not _quotes and time.monotonic() >= _quotes_retry_after:
        _quotes[:] = _fetch_quotes()
        if not _quotes:
            # Back off before trying again so a dead third-party API costs one
            # slow request every 10 minutes instead of one on every page load.
            _quotes_retry_after = time.monotonic() + _QUOTE_FAILURE_BACKOFF

    if _quotes:
        quote, author = _quotes.pop()
        cookie_val = f"{quote} - {author}"
        return quote, author, cookie_val

    # Cache empty AND API failed — fall back to cookie
    cookie_val = request.COOKIES.get(_QUOTE_COOKIE)
    if cookie_val:
        return cookie_val, None, None

    return _QUOTE_FALLBACK, None, None


def _pending_payments_card(current_month: int, current_year: int) -> dict:
    """The "Pagos pendientes" card: the count, five names, and the full list.

    One query. The per-student grouping is done in Python rather than as a
    second aggregate because the card needs both the individual amounts and the
    names, and the rows are already in memory.
    """
    pending_payments = Payment.objects.filter(
        payment_status="pending",
        due_date__month=current_month,
        due_date__year=current_year,
    ).select_related("student")

    pending_count = pending_payments.count()

    # Annotated: each entry holds two strings and a list of Decimals, so an
    # unannotated literal leaves the values typed `object` and every read of one
    # (sum, sort key, dict lookup) becomes an error.
    pending_by_student: dict[int, dict[str, Any]] = {}
    for payment in pending_payments:
        sid = payment.student_id
        if sid not in pending_by_student:
            pending_by_student[sid] = {
                "first_name": payment.student.first_name,
                "last_name": payment.student.last_name,
                "amounts": [],
            }
        pending_by_student[sid]["amounts"].append(payment.amount)

    pending_students_list = list(pending_by_student.values())
    all_pending_students = sorted(
        [
            {
                "display_name": "{} {}".format(
                    v["first_name"],
                    "".join(w[0].upper() + "." for w in v["last_name"].split() if w),
                ),
                "amount": sum(v["amounts"]),
            }
            for v in pending_students_list
        ],
        # `str(...)`: the dict is heterogeneous (str + Decimal), so a checker
        # types the value as `object`, which is not sortable.
        key=lambda x: str(x["display_name"]),
    )

    return {
        "pending_payments_count": pending_count,
        "pending_students": [v["first_name"] for v in pending_students_list[:5]],
        "has_more_pending": len(pending_students_list) > 5,
        "total_pending_students": len(pending_students_list),
        "all_pending_students": all_pending_students,
    }


def _birthdays_card(today: date) -> dict:
    """Birthdays this MONTH (the card) and TODAY (the greeting) — two queries.

    Deliberately two: the month list is ordered by day and capped at five for
    display but its full length is the badge count, while today's list is a
    separate `[:5]` slice used for a different sentence.
    """
    birthday_students = list(
        Student.objects.filter(active=True, birth_date__month=today.month).order_by("birth_date__day")
    )
    today_birthday_students = Student.objects.filter(
        active=True,
        birth_date__month=today.month,
        birth_date__day=today.day,
    ).order_by("first_name")[:5]

    return {
        "birthday_count": len(birthday_students),
        "birthdays": [{"name": s.first_name, "day": s.birth_date.day, "age": s.age} for s in birthday_students[:5]],
        "has_more_birthdays": len(birthday_students) > 5,
        "today_birthday_names": [s.first_name for s in today_birthday_students],
    }


def _upcoming_events_card(today: date) -> dict:
    """The scheduled-email sends coming up. No queries — `SCHEDULED_APPS` is a
    module constant and the cadences are pure date arithmetic."""
    # A rolling 35-day window, not "the rest of this month": the old scan died at
    # the month boundary (from the last Friday of September to the 30th the card
    # showed ZERO upcoming sends while three were pending), and `monthly_day_1`
    # gated on `d >= today` could only ever match on the 1st itself. The window
    # always contains the next occurrence of every weekly/monthly cadence.
    window = [today + timedelta(days=offset) for offset in range(35)]
    upcoming_events = []
    for app in SCHEDULED_APPS:
        if not app.get("active"):
            continue
        frequency = app["frequency"]
        occurrences: list[date] = []
        if frequency == "every_friday":
            occurrences = [d for d in window if d.weekday() == 4]
        elif frequency == "monthly_day_1":
            occurrences = [d for d in window if d.day == 1]
        elif frequency == "monthly_last_day":
            occurrences = [d for d in window if d.day == cal_module.monthrange(d.year, d.month)[1]]
        elif frequency == "daily":
            # One entry, not 35 — "the next send is today" is all the card needs.
            occurrences = [today]
        elif frequency == "yearly_april":
            occurrences = [d for d in window if d.month == 4 and d.day == 1]
        # manual / on_student_creation / on_enrollment / quarterly are event-driven,
        # not calendar-scheduled — correctly absent from the upcoming list.

        for d in occurrences:
            upcoming_events.append(
                {
                    "name": app["name"],
                    "date": d,
                    "url_name": app["url_name"],
                    "is_fun_friday": app["name"] == "Fun Friday",
                }
            )

    upcoming_events.sort(key=lambda x: cast(date, x["date"]))

    # A weekly send (Fun Friday) yields one event per remaining Friday, so the card
    # listed the same name four times. Collapse repeats into a single entry showing
    # the two nearest dates. The card's number counts these grouped entries — one
    # per email that goes out this month, not one per individual send.
    grouped_events: list[dict] = []
    events_by_name: dict[str, dict] = {}
    for event in upcoming_events:
        entry = events_by_name.get(str(event["name"]))
        if entry is None:
            entry = {**event, "dates": [event["date"]], "has_more_dates": False}
            events_by_name[str(event["name"])] = entry
            grouped_events.append(entry)
        elif len(entry["dates"]) < MAX_EVENT_DATES:
            entry["dates"].append(event["date"])
        else:
            entry["has_more_dates"] = True

    return {
        "upcoming_events_count": len(grouped_events),
        "upcoming_events": grouped_events[:5],
    }


def _revenue_card(current_month: int, current_year: int) -> dict:
    """Expected vs collected for the month, in ONE aggregate.

    Two `Case/When` sums rather than two queries — and they ask genuinely
    different questions: expected is keyed on `due_date` and counts every LIVE
    status, collected is keyed on `payment_date` and counts only `completed`.
    """
    zero = Decimal("0.00")
    revenue_stats = Payment.objects.aggregate(
        # Excludes cancelled / failed / refunded — see LIVE_PAYMENT_STATUSES.
        expected_revenue=Sum(
            Case(
                When(
                    due_date__month=current_month,
                    due_date__year=current_year,
                    payment_status__in=LIVE_PAYMENT_STATUSES,
                    then="amount",
                ),
                default=Value(0),
                output_field=DecimalField(),
            )
        ),
        monthly_income_total=Sum(
            Case(
                When(
                    payment_status="completed",
                    payment_date__month=current_month,
                    payment_date__year=current_year,
                    then="amount",
                ),
                default=Value(0),
                output_field=DecimalField(),
            )
        ),
    )
    return {
        "expected_revenue": revenue_stats["expected_revenue"] or zero,
        "monthly_income_total": revenue_stats["monthly_income_total"] or zero,
    }


def _capacity_card() -> dict:
    """Waiting-list total and the groups that could take a waiter.

    Goes through `group_capacity_summary()`, which answers every group's
    occupancy in ONE annotated query. Reading `Group.enrolled_count` /
    `available_spots` / `is_full` per row instead costs four queries per group —
    that helper exists precisely to stop this page doing that.
    """

    capacity_rows = group_capacity_summary()
    return {
        "waiting_count": sum(row["waiting"] for row in capacity_rows),
        "groups_with_openings": [
            {"name": row["name"], "color": row["color"], "available": row["available"], "waiting": row["waiting"]}
            for row in capacity_rows
            if row["has_room_for_waiters"]
        ],
    }


def home(request):
    """The dashboard.

    Split into one builder per card in v1.29.5 — it was a single 203-line
    function assembling a twenty-key context, so "which query feeds which card"
    could only be answered by reading all of it. Each builder below owns one
    card and returns exactly the context keys that card renders; this function
    owns the order, the merge, and the quote cookie.

    Query count is unchanged (the builders are called once each and nothing is
    re-evaluated) — `tests/integration/test_query_cost_and_idempotency.py`
    pins it.
    """
    today = date.today()

    todos = list(TodoItem.objects.order_by("due_date", "created_at"))
    quote_text, quote_author, new_cookie = _get_quote(request)

    context = {
        **_pending_payments_card(today.month, today.year),
        **_birthdays_card(today),
        **_upcoming_events_card(today),
        **_revenue_card(today.month, today.year),
        **_capacity_card(),
        "todos": todos,
        "overdue_todos_count": sum(1 for t in todos if t.is_overdue),
        "today": today,
        "inspirational_quote": quote_text,
        "inspirational_author": quote_author,
    }

    response = render(request, "home.html", context)
    if new_cookie is not None:
        response.set_cookie(
            _QUOTE_COOKIE,
            new_cookie,
            max_age=_QUOTE_COOKIE_TTL,
            httponly=True,
            samesite="Lax",
        )
    return response


@admin_required
def all_info(request):
    DB_PAGE_SIZE = 20

    # ── Students sorting ──
    students_sort = request.GET.get("students_sort", "date_desc")
    students_order = {
        "id_asc": "id",
        "first_name_asc": "first_name",
        "last_name_asc": "last_name",
        "date_desc": "-created_at",
    }.get(students_sort, "-created_at")
    students_qs = get_active_students().order_by(students_order)

    # ── Group filter ──
    students_group = None
    raw_group = (request.GET.get("students_group") or "").strip()
    if raw_group:
        try:
            students_group = int(raw_group)
        except ValueError:
            students_group = None  # hand-edited URL: ignore rather than 500
        else:
            students_qs = students_qs.filter(group_id=students_group)

    students_paginator = Paginator(students_qs, DB_PAGE_SIZE)
    students_page = students_paginator.get_page(request.GET.get("students_page", 1))

    # ── Payments sorting ──
    payments_sort = request.GET.get("payments_sort", "date_desc")
    payments_order = {
        "date_desc": "-created_at",
        "student_asc": ("student__first_name", "student__last_name"),
    }.get(payments_sort, "-created_at")
    if isinstance(payments_order, tuple):
        payments_qs = get_all_payments_unrestricted().order_by(*payments_order)
    else:
        payments_qs = get_all_payments_unrestricted().order_by(payments_order)
    payments_paginator = Paginator(payments_qs, DB_PAGE_SIZE)
    payments_page = payments_paginator.get_page(request.GET.get("payments_page", 1))

    return render(
        request,
        "all_info.html",
        {
            "students": students_page,
            "students_sort": students_sort,
            "students_total": students_paginator.count,
            "students_group": students_group,
            "group_choices": Group.objects.filter(active=True).order_by("group_name"),
            "payments": payments_page,
            "payments_sort": payments_sort,
            "payments_total": payments_paginator.count,
        },
    )
