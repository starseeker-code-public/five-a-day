import logging
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_http_methods
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from billing.forms import EnrollmentForm
from billing.models import Enrollment, Payment, SiteConfiguration, relevant_academic_years
from core.models import FunFridayAttendance, HistoryLog
from students.forms import StudentForm
from students.models import Group, Parent, Student

logger = logging.getLogger(__name__)

# ============================================================================
# PARENT AND STUDENT MANAGEMENT - Parent-First Flow
# ============================================================================


def _create_enrollment_fee_payment(student, parent, enrollment, enrollment_form):
    """Create the one-time matrícula Payment for a freshly issued enrollment.

    Shared by ``StudentCreateView.form_valid`` and ``enroll_student`` (the
    "Nueva matrícula" modal) so the fee, the returning-student discount and the
    concept wording cannot drift between the two entry points.

    Due on the last day of the month the enrollment STARTS
    (``enrollment.enrollment_date``), not of the month the ficha was created —
    a student signed up today for a 1 November start owes the matrícula with
    the November fees. Returns the fee charged.
    """
    import calendar

    from billing.services.enrollment_service import EnrollmentService

    config = SiteConfiguration.get_config()

    # "Matrícula especial (€)" overrides the configured fee outright. It is
    # its own field: `manual_amount` prices the recurring cuota, so a
    # special monthly price left the matrícula on the standard rate.
    special_fee = enrollment_form.cleaned_data.get("special_enrollment_fee")
    enrollment_fee, returning_discount = EnrollmentService.compute_enrollment_fee(
        config,
        student,
        is_adult=student.is_adult,
        special_fee=special_fee,
        force_returning=enrollment_form.cleaned_data.get("is_returning_student", False),
        this_academic_year=enrollment.academic_year,
    )
    concept = f"Matrícula {enrollment.academic_year} — {student.full_name}"
    if special_fee:
        concept += " (matrícula especial)"
    elif returning_discount:
        concept += f" (dto. alumno recurrente −{returning_discount:.2f} €)"

    start = enrollment.enrollment_date or date.today()
    due_date = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])

    Payment.objects.create(
        student=student,
        parent=parent,
        enrollment=enrollment,
        payment_type="enrollment",
        payment_method="transfer",
        amount=enrollment_fee,
        currency="EUR",
        payment_status="pending",
        due_date=due_date,
        concept=concept,
    )
    return enrollment_fee


class StudentCreateView(CreateView):
    """
    Vista para crear un nuevo estudiante.
    Puede recibir un parent_id como parámetro GET para pre-vincular al padre.
    """

    model = Student
    form_class = StudentForm
    template_name = "student_create.html"

    def get_waiting_entry(self):
        """Waiting-list entry this enrollment came from (`?from_waiting=<id>`), if any."""
        from core.views.waiting_list import waiting_entry_from_request

        return waiting_entry_from_request(self.request)

    def get_initial(self):
        """Prefill from the waiting-list entry so nothing is retyped."""
        initial = super().get_initial()
        waiting = self.get_waiting_entry()
        if waiting:
            initial["first_name"] = waiting.first_name
            initial["last_name"] = waiting.last_name
            initial["birth_date"] = waiting.birth_date
            initial["group"] = waiting.group_id
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Success state from redirect
        if self.request.GET.get("success"):
            context["show_success"] = True
            context["success_student_name"] = self.request.GET.get("student_name", "")
            context["success_fee"] = self.request.GET.get("fee", "")
            context["success_create_sibling"] = self.request.GET.get("create_sibling", "")
            context["success_parent_id"] = self.request.GET.get("parent_id", "")
            return context  # Skip loading form data for success page

        mode = self.request.GET.get("mode", "normal")
        context["creation_mode"] = mode
        context["is_adult_mode"] = mode == "adult"
        context["is_waiting_mode"] = mode == "waiting"

        parent_id = self.request.GET.get("parent_id")
        if parent_id:
            # ValueError as well as DoesNotExist: `?parent_id=abc` raised
            # "invalid literal for int()" and 500'd the page.
            try:
                parent = Parent.objects.get(id=int(parent_id))
                context["parent"] = parent
                context["parent_id"] = parent_id
            except (Parent.DoesNotExist, TypeError, ValueError):
                messages.error(self.request, "El padre especificado no existe")

        if mode == "existing_parent":
            context["all_parents"] = Parent.objects.all().order_by("last_name", "first_name")

        waiting_entry = self.get_waiting_entry()
        if "enrollment_form" not in context:
            # A student promoted off the waiting list may carry enrollment
            # history (moving them onto the list cancels the enrollment, it
            # does not delete it) — that IS an "antiguo alumno", so pre-mark
            # the checkbox. The admin can still untick it.
            context["enrollment_form"] = EnrollmentForm(
                self.request.POST or None,
                initial={"is_returning_student": bool(waiting_entry and waiting_entry.enrollments.exists())},
            )

        context["groups"] = Group.objects.filter(active=True)
        context["waiting_entry"] = waiting_entry

        config = SiteConfiguration.get_config()
        # Quarterly = 3 * full_time - 5%
        quarterly_gross = config.full_time_monthly_fee * 3
        quarterly_price = quarterly_gross * (1 - config.quarterly_enrollment_discount / 100)
        context["price_config"] = {
            "monthly_full": str(config.full_time_monthly_fee),
            "monthly_part": str(config.part_time_monthly_fee),
            "quarterly": str(quarterly_price),
            # The pre-discount total (3 mensualidades). `quarterly` already has the
            # -5% baked in, so the price widget was striking through the discounted
            # figure and showing the very same number twice.
            "quarterly_gross": str(quarterly_gross),
            "adult_group": str(config.adult_group_monthly_fee),
        }
        context["enrollment_fee_children"] = str(config.children_enrollment_fee)
        context["enrollment_fee_adult"] = str(config.adult_enrollment_fee)
        context["language_cheque_discount"] = str(config.language_cheque_discount)
        context["sibling_discount"] = str(config.sibling_discount)
        context["returning_student_discount"] = str(config.returning_student_enrollment_discount)

        # First-period proration. A student joining part-way through a month pays
        # only the remaining days OF THAT MONTH, so the first fee differs from the
        # recurring one and the form has to say so before the admin saves.
        from billing.models import current_academic_year
        from billing.services.payment_service import MONTH_NAMES_ES, PaymentService

        today = date.today()
        sequence = PaymentService.teaching_months(current_academic_year(today))
        first = next(((m, y) for m, y in sequence if PaymentService._last_day(m, y) >= today), None)
        if first is not None:
            first_month, first_year = first
            fraction = PaymentService.proration_fraction(today, first_month, first_year)
            context["first_period_fraction"] = str(fraction)
            context["first_period_label"] = f"{MONTH_NAMES_ES.get(first_month, '')} {first_year}"
            context["first_period_is_partial"] = fraction != Decimal("1")
        else:
            context["first_period_fraction"] = "1"
            context["first_period_label"] = ""
            context["first_period_is_partial"] = False

        # Students for sibling search (active, current year)
        context["all_students_for_sibling"] = (
            Student.objects.filter(active=True).select_related("group").order_by("first_name", "last_name")[:200]
        )

        return context

    def form_valid(self, form):
        from comms.tasks import send_welcome_email_task

        is_waiting_mode = (
            self.request.POST.get("is_waiting") in ("on", "true", "1") or self.request.GET.get("mode") == "waiting"
        )
        is_adult_mode = self.request.POST.get("is_adult_mode") == "true"

        # For waiting-list students we skip the enrollment form entirely — no
        # plan/discount is chosen until the student is promoted off the list.
        if not is_waiting_mode:
            enrollment_form = EnrollmentForm(self.request.POST)
            if not enrollment_form.is_valid():
                return self.form_invalid(form)
        else:
            enrollment_form = None

        try:
            with transaction.atomic():
                student = form.save(commit=False)
                if is_adult_mode:
                    student.is_adult = True
                    student.gdpr_signed = True
                    student.email = self.request.POST.get("adult_email", "")
                    student.phone = self.request.POST.get("adult_phone", "")
                if is_waiting_mode:
                    student.is_waiting = True
                student.save()

                parent = None
                parent_id = None

                if not is_adult_mode:
                    parent_id = self.request.POST.get("parent_id") or self.request.GET.get("parent_id")
                    if not parent_id:
                        messages.error(self.request, "Debe especificar un padre para el estudiante")
                        student.delete()
                        return self.form_invalid(form)
                    try:
                        parent = Parent.objects.get(id=int(parent_id))
                        student.parents.add(parent)
                    except (Parent.DoesNotExist, TypeError, ValueError):
                        messages.error(self.request, "El padre especificado no existe")
                        student.delete()
                        return self.form_invalid(form)

                if is_waiting_mode:
                    HistoryLog.log(
                        "waiting_list_added",
                        # `Student.group` is nullable (v1.15) and the form field is
                        # not required, so a waiting entry taken over the phone
                        # legitimately has none. Unguarded, this raised
                        # AttributeError inside the atomic block and the whole
                        # creation failed with the generic "Error al crear el
                        # estudiante" — for exactly the case the nullable group
                        # was introduced to support. `waiting_list_create`
                        # already words it this way.
                        f"Nuevo en lista de espera: {student.full_name} — "
                        f"{student.group.group_name if student.group_id else 'sin grupo preferido'}",
                        icon="hourglass_top",
                    )
                    messages.success(
                        self.request,
                        f"✅ {student.full_name} añadido/a a la lista de espera.",
                    )
                    return HttpResponseRedirect(reverse("waiting_list"))

                # Create enrollment
                enrollment = enrollment_form.create_enrollment(student, is_adult=is_adult_mode)

                # Create enrollment fee payment (pending, due end of the month
                # the enrollment STARTS). Applies the returning-student discount
                # automatically when the student has any prior Enrollment for an
                # earlier academic year (v1.13).
                enrollment_fee = _create_enrollment_fee_payment(student, parent, enrollment, enrollment_form)

                # Issue the period the student joined, prorated for the days
                # already gone. Later periods are opened by the generate_payments
                # cron on the 1st; this call is idempotent against it.
                from billing.services.payment_service import PaymentService

                PaymentService.schedule_academic_year_payments(enrollment, parent)

                # Came from the waiting list: the real student now exists with a
                # parent and an enrollment, so drop the placeholder entry.
                waiting = self.get_waiting_entry()
                if waiting and waiting.id != student.id:
                    from core.views.waiting_list import discard_waiting_entry

                    discard_waiting_entry(waiting, student)

                HistoryLog.log(
                    "student_enrolled",
                    f"Alumno matriculado: {student.full_name} — {enrollment.get_schedule_type_display()}",
                    icon="school",
                )

                # Enqueue welcome email AFTER the transaction commits.
                # In Celery eager mode (dev / no Redis) the task runs
                # synchronously — if we queued inside the atomic block and a
                # later step raised, the transaction would roll back but the
                # parent would already have received an email about a student
                # that never existed. `transaction.on_commit` defers the
                # dispatch until COMMIT succeeds.
                _parent_id = parent.id if parent else None
                _enrollment_id = enrollment.id
                _student_id = student.id

                def _queue_welcome():
                    try:
                        send_welcome_email_task.delay(
                            parent_id=_parent_id,
                            student_id=_student_id,
                            enrollment_id=_enrollment_id,
                        )
                    except Exception:
                        pass  # never fail the request over email dispatch

                transaction.on_commit(_queue_welcome)

                # Redirect to success page with student info
                from urllib.parse import quote

                return HttpResponseRedirect(
                    reverse("student_create")
                    + f"?success=1&student_name={quote(student.full_name)}&student_id={student.id}"
                    + f"&fee={enrollment_fee}"
                    + (
                        f"&parent_id={parent_id}&create_sibling=1"
                        if "create_sibling" in self.request.POST and parent_id
                        else ""
                    )
                )

        except Exception:
            # Never echo str(e): a double submit raises IntegrityError whose
            # text carries the Postgres constraint, table and column names.
            logger.exception("Error creating student")
            messages.error(
                self.request,
                "Error al crear el estudiante. Revisa los datos e inténtalo de nuevo.",
            )
            return self.form_invalid(form)

    def form_invalid(self, form):
        messages.error(
            self.request,
            "No se pudo crear el estudiante. Revisa los campos obligatorios.",
        )
        context = self.get_context_data(form=form)
        context["enrollment_form"] = EnrollmentForm(self.request.POST)
        return self.render_to_response(context)


#: Max student rows sent to the browser in one page load. The table filters and
#: sorts CLIENT-side (every row carries `data-*` attributes that `students.js`
#: reads), so server pagination would silently reduce "search all students" to
#: "search this page". The cap bounds the response instead, and
#: `result_truncated` surfaces it rather than dropping rows quietly — the same
#: shape `payments_list` already uses. The academy is sized for ~2,000 students,
#: so this is the ceiling that matters.
_STUDENT_LIST_CAP = 500


class StudentListView(ListView):
    """Vista para listar todos los estudiantes"""

    model = Student
    template_name = "students.html"
    context_object_name = "students"

    def get_queryset(self):
        # Both cohorts during the May–August overlap: students finishing the
        # running course and those already enrolled for the next one. Filtering
        # on one year alone makes half the academy vanish from the list.
        academic_years = relevant_academic_years()
        queryset = (
            Student.objects.filter(
                active=True,
                is_waiting=False,
                enrollments__academic_year__in=academic_years,
            )
            .distinct()
            .select_related("group")
            .prefetch_related("parents", "enrollments__enrollment_type")
        )

        search_query = self.request.GET.get("search", "").strip()
        if search_query:
            queryset = queryset.filter(Q(first_name__icontains=search_query) | Q(last_name__icontains=search_query))

        # `_total_count` is stashed for get_context_data so the truncation
        # notice can report the real figure, not the capped one.
        queryset = queryset.order_by("-created_at")
        self._total_count = queryset.count()
        return queryset[:_STUDENT_LIST_CAP]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search_query"] = self.request.GET.get("search", "")
        total = getattr(self, "_total_count", 0)
        context["total_count"] = total
        context["result_truncated"] = total > _STUDENT_LIST_CAP
        context["list_cap"] = _STUDENT_LIST_CAP
        context["groups"] = Group.objects.filter(active=True)
        # `context["parents"]` used to be an unbounded `Parent.objects.all()` here.
        # No template consumes it (students.html reads `student.parents.all`, which is
        # prefetched), so it was dead weight one edit away from becoming a full-table
        # render on a 2,000-student academy.

        # The "Nueva matrícula" modal (book icon) renders the shared
        # EnrollmentForm, so its plan choices, field ids and the start-date
        # default (today) cannot drift from the student-create page.
        context["enrollment_form"] = EnrollmentForm()

        context["this_week_ids"] = get_ff_student_ids(get_next_friday())
        context["last_week_ids"] = get_ff_student_ids(get_last_friday())

        # Language cheque info for current academic year
        academic_years = relevant_academic_years()
        lc_student_ids = set(
            Enrollment.objects.filter(
                academic_year__in=academic_years,
                has_language_cheque=True,
                student__active=True,
            ).values_list("student_id", flat=True)
        )
        context["language_cheque_ids"] = lc_student_ids
        context["language_cheque_count"] = len(lc_student_ids)

        return context


class StudentUpdateView(UpdateView):
    """Vista para actualizar un estudiante existente"""

    model = Student
    form_class = StudentForm
    template_name = "student_update.html"
    pk_url_kwarg = "student_id"

    def get_success_url(self):
        return reverse_lazy("students_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Obtener la matrícula activa
        try:
            enrollment = self.object.enrollments.filter(status="active").latest("created_at")
        except Enrollment.DoesNotExist:
            enrollment = None

        # Pre-fill enrollment form from current enrollment
        if "enrollment_form" not in context:
            initial = {}
            if enrollment:
                # Map back to plan choice
                if enrollment.payment_modality == "quarterly":
                    initial["enrollment_plan"] = "quarterly"
                elif enrollment.schedule_type == "part_time":
                    initial["enrollment_plan"] = "monthly_part"
                else:
                    initial["enrollment_plan"] = "monthly_full"
                initial["discount"] = str(int(enrollment.discount_percentage))
                initial["has_language_cheque"] = enrollment.has_language_cheque
                initial["is_sibling_discount"] = enrollment.is_sibling_discount
            context["enrollment_form"] = EnrollmentForm(self.request.POST or None, initial=initial)

        context["parents"] = self.object.parents.all()
        context["groups"] = Group.objects.filter(active=True)

        return context

    @staticmethod
    def _enrollment_plan_changed(current, enrollment_form) -> bool:
        """True when the submitted plan differs from the active enrollment.

        Guards against re-issuing an identical enrollment on every save. Maps
        the form's single `enrollment_plan` choice back onto the two model
        fields it encodes (payment_modality + schedule_type).
        """
        data = enrollment_form.cleaned_data
        plan = data.get("enrollment_plan") or "monthly_full"
        if plan == "quarterly":
            wanted = ("quarterly", "full_time")
        elif plan == "monthly_part":
            wanted = ("monthly", "part_time")
        else:
            wanted = ("monthly", "full_time")

        # Adult enrollments always resolve to adult_group/monthly regardless of
        # the plan widget, so compare only the discount flags for them.
        if current.schedule_type != "adult_group" and (current.payment_modality, current.schedule_type) != wanted:
            return True
        return current.has_language_cheque != bool(
            data.get("has_language_cheque")
        ) or current.is_sibling_discount != bool(data.get("is_sibling_discount"))

    def form_valid(self, form):
        from billing.services.enrollment_service import EnrollmentService

        # Waiting-list students don't have an active enrollment, so we skip the
        # enrollment form. Once is_waiting is toggled off, a fresh enrollment is
        # created below.
        is_waiting_now = form.cleaned_data.get("is_waiting", False)
        student_pk = self.object.pk if self.object else None

        enrollment_form = None
        if not is_waiting_now:
            enrollment_form = EnrollmentForm(self.request.POST)
            if not enrollment_form.is_valid():
                return self.form_invalid(form)

        try:
            with transaction.atomic():
                student = form.save()

                if is_waiting_now:
                    # Cancel any active enrollment — the student is off the roster.
                    EnrollmentService.close_active_enrollments(student, "cancelled")
                else:
                    # Only re-issue the enrollment when the plan actually
                    # changed. This used to run unconditionally, so saving an
                    # edit to (say) the school name marked the current
                    # enrollment "finished" and created a duplicate — students
                    # accumulated a new enrollment row per edit, and payments
                    # then attached to whichever one came back first.
                    current = student.enrollments.filter(status="active").order_by("-enrollment_date", "-id").first()
                    if current is None or self._enrollment_plan_changed(current, enrollment_form):
                        EnrollmentService.close_active_enrollments(student, "finished")
                        enrollment_form.create_enrollment(student, is_adult=student.is_adult)

                messages.success(
                    self.request,
                    f"Estudiante {student.full_name} actualizado exitosamente",
                )

        except Exception:
            logger.exception("Error updating student %s", student_pk)
            messages.error(
                self.request,
                "Error al actualizar el estudiante. Revisa los datos e inténtalo de nuevo.",
            )
            return self.form_invalid(form)

        return HttpResponseRedirect(self.get_success_url())

    def form_invalid(self, form):
        context = self.get_context_data(form=form)
        context["enrollment_form"] = EnrollmentForm(self.request.POST)
        return self.render_to_response(context)


class StudentDetailView(DetailView):
    """Vista para ver detalles de un estudiante"""

    model = Student
    template_name = "student_detail.html"
    context_object_name = "student"
    pk_url_kwarg = "student_id"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["parents"] = self.object.parents.all()
        # `enrollment.enrollment_type.display_name` is rendered per row.
        context["enrollments"] = self.object.enrollments.select_related("enrollment_type").order_by("-created_at")
        # Most recent first, by DUE date — the month the payment is for, which is
        # what the table's "Vencimiento" column shows and what `payments_list`
        # orders by, so the two pages agree.
        #
        # Was `-payment_date`, which is NULL on every pending payment: Postgres
        # sorts NULLs FIRST on a DESC ordering, so the whole unpaid backlog piled
        # up above the history in no particular order. `due_date` is NOT NULL, so
        # the ordering is total; `-id` breaks ties between payments due the same
        # day (the matrícula and the first cuota, typically).
        context["payments"] = Payment.objects.filter(student=self.object).order_by("-due_date", "-id")
        context["fun_friday_dates"] = self.object.fun_friday_dates.all()
        return context


# ============================================================================
# FUN FRIDAY DATE HELPERS  (reusable across views)
# ============================================================================


def get_next_friday(from_date=None):
    """Return this week's Friday (today if today is Friday, else next Friday)."""
    from datetime import date as _date
    from datetime import timedelta

    if from_date is None:
        from_date = _date.today()
    days_ahead = (4 - from_date.weekday()) % 7
    return from_date if days_ahead == 0 else from_date + timedelta(days=days_ahead)


def get_last_friday(from_date=None):
    """Return last week's Friday (7 days before get_next_friday)."""
    from datetime import timedelta

    return get_next_friday(from_date) - timedelta(days=7)


def get_ff_student_ids(friday_date):
    """Return a set of student IDs registered for the given Friday."""
    return set(FunFridayAttendance.objects.filter(date=friday_date).values_list("student_id", flat=True))


# ============================================================================
# STUDENT HELPER FBVs
# ============================================================================


def search_students(request):
    """AJAX endpoint to search active students by name (JSON).

    Returns ``{"results": [{id, full_name, school, parent_id, parent_name}]}``.

    The parent is included in THIS response on purpose. Picking a student in the
    create-payment form used to fire a second request to
    ``validate_student_parent`` with ``parent_id: 0``, whose handler returned
    ``{"valid": false, "parents": [...]}`` — a lookup dressed up as a validation
    — and whose failure was swallowed by a bare ``.catch(() => {})``. Any hiccup
    on that hop (a 403, a dropped request) left "Padre/Tutor" silently empty with
    nothing in the UI to say why. One request, filled in synchronously, has no
    such failure mode.

    ``parent_id`` is ``None`` for an adult student, who legitimately has no
    parent/guardian; the form treats that as valid rather than as missing data.
    """
    query = request.GET.get("q", "").strip()

    if len(query) < 2:
        return JsonResponse({"results": []})

    students = (
        Student.objects.filter(active=True)
        .filter(Q(first_name__icontains=query) | Q(last_name__icontains=query))
        .select_related("group")
        # `order_by("id")` is not cosmetic: the parent returned here is the one
        # the create-payment form fills into "Padre/Tutor", i.e. the TITULAR of
        # the payment about to be created. `enroll_student` and both payment
        # generators pick the titular with an explicit `order_by("id")`, and no
        # model in `students.models` sets `Meta.ordering` — so an unordered read
        # here could name a different parent than the one the schedule bills,
        # for the same student.
        .prefetch_related(Prefetch("parents", queryset=Parent.objects.order_by("id")))[:10]
    )

    results = []
    for s in students:
        # `next(iter(...))` and not `.first()`: `.first()` on an unordered
        # queryset adds `order_by("pk")`, which clones the queryset and drops
        # the prefetch cache — one extra query per student, ten per keystroke.
        parent = next(iter(s.parents.all()), None)
        results.append(
            {
                "id": s.id,
                "full_name": s.full_name,
                "school": s.school or "",
                "parent_id": parent.id if parent else None,
                "parent_name": parent.full_name if parent else "",
            }
        )
    return JsonResponse({"results": results})


@require_http_methods(["POST"])
def enroll_student(request, student_id):
    """AJAX: issue a NEW enrollment for an existing student (the book icon on the list).

    Mirrors the enrollment leg of ``StudentCreateView.form_valid`` for a student
    who already has a ficha: finishes the current active enrollment (the DB
    enforces one active enrollment per student), creates the new one from the
    same ``EnrollmentForm``, optionally charges the matrícula, and issues the
    periodic payments from the chosen start date — ``billing_periods`` reads
    ``enrollment.enrollment_date``, so a student re-enrolled today with a
    1 November start is billed from November, not from today.

    Returns ``{"success": bool, ...}`` JSON, like every AJAX endpoint.
    """
    student = get_object_or_404(Student, id=student_id, active=True)

    enrollment_form = EnrollmentForm(request.POST)
    if not enrollment_form.is_valid():
        # Form ValidationError text is Django's written-for-humans copy — safe
        # (and useful) to echo, unlike exception text.
        error_text = " ".join(msg for errors in enrollment_form.errors.values() for msg in errors)
        return JsonResponse(
            {"success": False, "error": error_text or "Datos de matrícula no válidos."},
            status=400,
        )

    charge_fee = request.POST.get("charge_enrollment_fee") in ("on", "true", "1")

    try:
        from billing.services.enrollment_service import EnrollmentService
        from billing.services.payment_service import PaymentService

        with transaction.atomic():
            # One ACTIVE enrollment per student (DB constraint) — the new
            # matrícula supersedes the current one, same as StudentUpdateView
            # does on a plan change.
            EnrollmentService.close_active_enrollments(student, "finished")

            enrollment = enrollment_form.create_enrollment(student, is_adult=student.is_adult)

            # Adults legitimately have no parent/guardian; for children the
            # first parent is the titular — same explicit ordering the payment
            # generators use.
            parent = None if student.is_adult else student.parents.order_by("id").first()

            enrollment_fee = None
            if charge_fee:
                enrollment_fee = _create_enrollment_fee_payment(student, parent, enrollment, enrollment_form)

            payments_created = PaymentService.schedule_academic_year_payments(enrollment, parent)

            HistoryLog.log(
                "student_enrolled",
                f"Nueva matrícula: {student.full_name} — {enrollment.academic_year} "
                f"({enrollment.get_schedule_type_display()})",
                icon="school",
            )
    except Exception:
        # Never echo str(e): an IntegrityError carries table/constraint names.
        logger.exception("Error creating new enrollment for student %d", int(student_id))
        return JsonResponse(
            {"success": False, "error": "Error al crear la matrícula. Revisa los datos e inténtalo de nuevo."},
            status=500,
        )

    return JsonResponse(
        {
            "success": True,
            "enrollment_id": enrollment.id,
            "academic_year": enrollment.academic_year,
            "payments_created": payments_created,
            "enrollment_fee": str(enrollment_fee) if enrollment_fee is not None else None,
        }
    )
