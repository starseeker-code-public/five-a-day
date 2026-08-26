"""Expense CRUD + list view (v1.5)."""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from billing.models import Expense
from billing.services.expense_service import monthly_totals


def expenses_list(request):
    """Table of every expense with a month/category filter."""
    today = date.today()
    try:
        month = int(request.GET.get("month") or today.month)
        year = int(request.GET.get("year") or today.year)
    except (TypeError, ValueError):
        month, year = today.month, today.year

    category = request.GET.get("category", "").strip()

    qs = Expense.objects.filter(is_recurring=False, expense_date__month=month, expense_date__year=year)
    if category:
        qs = qs.filter(category=category)

    templates = Expense.objects.filter(is_recurring=True).order_by("category", "description")
    totals = monthly_totals(month, year)

    return render(
        request,
        "expenses.html",
        {
            "expenses": qs.order_by("-expense_date", "-created_at"),
            "templates": templates,
            "month": month,
            "year": year,
            "category": category,
            "categories": Expense.EXPENSE_CATEGORY_CHOICES,
            "frequencies": Expense.RECURRING_FREQUENCY_CHOICES,
            "weekday_choices": Expense.WEEKDAY_CHOICES,
            "totals": totals,
        },
    )


def _parse_amount(raw: str) -> Decimal | None:
    if not raw:
        return None
    try:
        return Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return None


@require_http_methods(["POST"])
def create_expense(request):
    description = (request.POST.get("description") or "").strip()
    category = (request.POST.get("category") or "other").strip()
    amount = _parse_amount(request.POST.get("amount") or "")
    expense_date_str = request.POST.get("expense_date") or date.today().isoformat()
    notes = (request.POST.get("notes") or "").strip()
    is_recurring = request.POST.get("is_recurring") in ("on", "true", "1")
    recurring_frequency = (request.POST.get("recurring_frequency") or "monthly").strip()
    recurring_day_raw = request.POST.get("recurring_day") or ""

    if not description or amount is None or amount <= 0:
        messages.error(request, "Debes indicar una descripción y un importe válido.")
        return redirect("expenses_list")

    try:
        parsed_date = date.fromisoformat(expense_date_str)
    except ValueError:
        parsed_date = date.today()

    recurring_day = None
    recurring_month = None
    recurring_weekdays = ""
    if is_recurring:
        if recurring_frequency not in ("monthly", "yearly", "weekly"):
            recurring_frequency = "monthly"

        if recurring_frequency == "weekly":
            selected = request.POST.getlist("recurring_weekdays")
            valid = sorted({int(d) for d in selected if d.isdigit() and 0 <= int(d) <= 6})
            if not valid:
                messages.error(request, "Un gasto semanal debe tener al menos un día de la semana.")
                return redirect("expenses_list")
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

    Expense.objects.create(
        description=description,
        category=category,
        amount=amount,
        expense_date=parsed_date,
        notes=notes,
        is_recurring=is_recurring,
        recurring_frequency=recurring_frequency,
        recurring_day=recurring_day,
        recurring_month=recurring_month,
        recurring_weekdays=recurring_weekdays,
    )
    messages.success(request, f"✅ Gasto '{description}' guardado.")
    return redirect("expenses_list")


@require_http_methods(["POST"])
def delete_expense(request, expense_id):
    expense = get_object_or_404(Expense, id=expense_id)
    expense.delete()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True})
    messages.success(request, "Gasto eliminado.")
    return redirect("expenses_list")


__all__ = ["expenses_list", "create_expense", "delete_expense"]
