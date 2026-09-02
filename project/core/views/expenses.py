"""Expense CRUD + list view (v1.5)."""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from billing.models import Expense
from billing.services import gcp_cost_service
from billing.services.expense_service import monthly_totals
from core.utils import MAX_QUERY_YEAR, MIN_QUERY_YEAR, safe_int


def _default_expense_date(month: int, year: int, today: date) -> date:
    """Date to prefill the "Nuevo gasto" form with.

    Today when the filter is on the current month, otherwise the 1st of the
    period being looked at — adding a gasto while browsing March should not
    silently date it today and drop it out of the list you are staring at.
    """
    if (month, year) == (today.month, today.year):
        return today
    try:
        return date(year, month, 1)
    except ValueError:
        return today


def expenses_list(request):
    """Table of every expense with a month/category filter."""
    today = date.today()
    # Range-checked, not just parsed: `expense_date__year` makes Django build a
    # real date for the bounds, so `?year=-5` and `?year=99999999999` were 500s
    # that sailed straight through an `except (TypeError, ValueError)`.
    month = safe_int(request.GET.get("month"), default=today.month, low=1, high=12)
    year = safe_int(request.GET.get("year"), default=today.year, low=MIN_QUERY_YEAR, high=MAX_QUERY_YEAR)

    category = request.GET.get("category", "").strip()

    qs = Expense.objects.filter(is_recurring=False, expense_date__month=month, expense_date__year=year)
    if category:
        qs = qs.filter(category=category)

    templates = Expense.objects.filter(is_recurring=True).order_by("category", "description")
    totals = monthly_totals(month, year)

    # The RUNNING month's GCP spend is dynamic — read live (cached) from the
    # billing export and folded into the displayed totals, never persisted.
    # Once the month closes, `archive_gcp_costs` stores it as a real `software`
    # Expense row and this block goes quiet for that month (the archived-row
    # check keeps a same-month row from ever being counted twice). Past months
    # therefore show only saved values.
    gcp_live = None
    if (
        (month, year) == (today.month, today.year)
        and gcp_cost_service.is_configured()
        and gcp_cost_service.archived_gcp_expense(year, month) is None
    ):
        gcp_live = gcp_cost_service.month_cost(year, month)
        if gcp_live is not None and gcp_live > 0:
            totals["expenses"] += gcp_live
            totals["net"] -= gcp_live
        else:
            gcp_live = None

    return render(
        request,
        "expenses.html",
        {
            "expenses": qs.order_by("-expense_date", "-created_at"),
            "templates": templates,
            "month": month,
            "year": year,
            "category": category,
            # The "Nueva fecha" input is <input type="date">, which only accepts
            # YYYY-MM-DD. It used to be given "{{ month }}-{{ year }}" ("8-2026"),
            # which every browser rejects, so the field always rendered blank.
            "default_expense_date": _default_expense_date(month, year, today),
            "categories": Expense.EXPENSE_CATEGORY_CHOICES,
            "frequencies": Expense.RECURRING_FREQUENCY_CHOICES,
            "weekday_choices": Expense.WEEKDAY_CHOICES,
            "totals": totals,
            # The pseudo-row only renders when the category filter allows it,
            # but the figure is folded into `totals` regardless (the summary
            # cards are month-wide, exactly like `monthly_totals`).
            "gcp_live": gcp_live if category in ("", "software") else None,
            "gcp_live_date": today,
            "gcp_live_description": gcp_cost_service.GCP_EXPENSE_DESCRIPTION,
        },
    )


def _parse_amount(raw: str) -> Decimal | None:
    """Parse the amount field, or None when it is not a usable number.

    `Decimal()` accepts "NaN" and "Infinity" — they are valid Decimals, not
    parse errors — so they slipped past the `except InvalidOperation` and blew
    up on the very next line: `Decimal("NaN") <= 0` raises InvalidOperation
    itself, which nothing here catches. That turned `amount=NaN` on a form any
    non-admin teacher can reach into an unhandled 500. `is_finite()` rejects
    both, and the caller then reports the normal "importe válido" message.
    """
    if not raw:
        return None
    try:
        value = Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


_VALID_CATEGORIES = {value for value, _ in Expense.EXPENSE_CATEGORY_CHOICES}
_VALID_FREQUENCIES = {value for value, _ in Expense.RECURRING_FREQUENCY_CHOICES}


def _expense_fields_from(request):
    """Parse an expense create/update POST.

    Returns `(fields, error)`. `error` is a Spanish message ready for
    `messages.error`; when it is set, `fields` is None.

    Shared by `create_expense` and `update_expense` so the two can never drift —
    an edit that parsed its recurrence differently from the create would quietly
    change a template's cadence.
    """
    description = (request.POST.get("description") or "").strip()
    category = (request.POST.get("category") or "other").strip()
    amount = _parse_amount(request.POST.get("amount") or "")
    expense_date_str = request.POST.get("expense_date") or date.today().isoformat()
    notes = (request.POST.get("notes") or "").strip()
    is_recurring = request.POST.get("is_recurring") in ("on", "true", "1")
    recurring_frequency = (request.POST.get("recurring_frequency") or "monthly").strip()
    recurring_day_raw = request.POST.get("recurring_day") or ""

    if not description or amount is None or amount <= 0:
        return None, "Debes indicar una descripción y un importe válido."

    # `category` is a free-form POST value and `Model.save()` does not enforce
    # `choices`, so an unknown slug used to persist and then render as a blank
    # category in the list and the totals breakdown.
    if category not in _VALID_CATEGORIES:
        category = "other"

    try:
        parsed_date = date.fromisoformat(expense_date_str)
    except ValueError:
        parsed_date = date.today()

    recurring_day = None
    recurring_month = None
    recurring_weekdays = ""
    if is_recurring:
        if recurring_frequency not in _VALID_FREQUENCIES:
            recurring_frequency = "monthly"

        if recurring_frequency == "weekly":
            selected = request.POST.getlist("recurring_weekdays")
            valid = sorted({int(d) for d in selected if d.isdigit() and 0 <= int(d) <= 6})
            if not valid:
                return None, "Un gasto semanal debe tener al menos un día de la semana."
            recurring_weekdays = ",".join(str(d) for d in valid)
        else:
            try:
                recurring_day = int(recurring_day_raw)
            except ValueError:
                recurring_day = 1
            # 1-31. Days past the end of a short month are clamped to that
            # month's last day at materialisation time, so 31 behaves as
            # "last day of the month" (this used to be silently capped at 28).
            recurring_day = max(1, min(recurring_day, 31))
            if recurring_frequency == "yearly":
                try:
                    recurring_month = int(request.POST.get("recurring_month") or 1)
                except ValueError:
                    recurring_month = 1
                recurring_month = max(1, min(recurring_month, 12))
    else:
        recurring_frequency = "monthly"

    return {
        "description": description,
        "category": category,
        "amount": amount,
        "expense_date": parsed_date,
        "notes": notes,
        "is_recurring": is_recurring,
        "recurring_frequency": recurring_frequency,
        "recurring_day": recurring_day,
        "recurring_month": recurring_month,
        "recurring_weekdays": recurring_weekdays,
    }, None


@require_http_methods(["POST"])
def create_expense(request):
    fields, error = _expense_fields_from(request)
    if error:
        messages.error(request, error)
        return redirect("expenses_list")

    expense = Expense(**fields)
    try:
        # `Expense.clean()` carries the real per-frequency rules and neither
        # `create()` nor `save()` runs it, so an invalid recurrence used to reach
        # the database and simply never materialise.
        expense.full_clean()
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("expenses_list")

    expense.save()
    messages.success(request, f"✅ Gasto '{expense.description}' guardado.")
    return redirect("expenses_list")


@require_http_methods(["POST"])
def update_expense(request, expense_id):
    """Edit an existing expense or recurring template.

    There was no update path at all: the only way to change the rent after a
    rise was to delete the template and build a new one, which orphaned every
    row already generated from it (`generated_from` is SET_NULL) and lost the
    cadence. Editing the template in place keeps that history attached.

    Rows already materialised for past months are deliberately NOT rewritten —
    they are what the academy actually paid, and back-dating them would corrupt
    the monthly totals. A new amount applies from the next materialisation.
    """
    expense = get_object_or_404(Expense, id=expense_id)

    fields, error = _expense_fields_from(request)
    if error:
        messages.error(request, error)
        return redirect("expenses_list")

    for field, value in fields.items():
        setattr(expense, field, value)

    try:
        expense.full_clean()
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("expenses_list")

    expense.save()
    label = "Plantilla" if expense.is_recurring else "Gasto"
    messages.success(request, f"✅ {label} '{expense.description}' actualizado.")
    return redirect("expenses_list")


@require_http_methods(["POST"])
def delete_expense(request, expense_id):
    expense = get_object_or_404(Expense, id=expense_id)
    expense.delete()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True})
    messages.success(request, "Gasto eliminado.")
    return redirect("expenses_list")


__all__ = ["expenses_list", "create_expense", "update_expense", "delete_expense"]
