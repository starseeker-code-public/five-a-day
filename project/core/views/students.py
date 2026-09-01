import logging
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
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

        if "enrollment_form" not in context:
            context["enrollment_form"] = EnrollmentForm(self.request.POST or None)

        context["groups"] = Group.objects.filter(active=True)
        context["waiting_entry"] = self.get_waiting_entry()

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
        import calendar

        from comms.tasks import send_welcome_email_task
        from core.models import HistoryLog

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
                        f"Nuevo en lista de espera: {student.full_name} — {student.group.group_name}",
                        icon="hourglass_top",
                    )
                    messages.success(
                        self.request,
                        f"✅ {student.full_name} añadido/a a la lista de espera.",
                    )
                    return HttpResponseRedirect(reverse("waiting_list"))

                # Create enrollment
                enrollment = enrollment_form.create_enrollment(student, is_adult=is_adult_mode)

                # Create enrollment fee payment (pending, due end of month).
                # Applies the returning-student discount automatically when
                # the student has any prior Enrollment for an earlier
                # academic year (v1.13).
                from billing.services.enrollment_service import EnrollmentService

                config = SiteConfiguration.get_config()
                today = date.today()
                last_day = calendar.monthrange(today.year, today.month)[1]
                due_date = date(today.year, today.month, last_day)

                # "Matrícula especial (€)" overrides the configured fee outright. It is
                # its own field: `manual_amount` prices the recurring cuota, so a
                # special monthly price left the matrícula on the standard rate.
                special_fee = enrollment_form.cleaned_data.get("special_enrollment_fee")
                enrollment_fee, returning_discount = EnrollmentService.compute_enrollment_fee(
                    config, student, is_adult=is_adult_mode, special_fee=special_fee
                )
                concept = f"Matrícula {enrollment.academic_year} — {student.full_name}"
                if special_fee:
                    concept += " (matrícula especial)"
                elif returning_discount:
                    concept += f" (dto. alumno recurrente −{returning_discount:.2f} €)"

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

        return queryset.order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search_query"] = self.request.GET.get("search", "")
        context["groups"] = Group.objects.filter(active=True)
        context["parents"] = Parent.objects.all()

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
                    student.enrollments.filter(status="active").update(status="cancelled")
                else:
                    # Only re-issue the enrollment when the plan actually
                    # changed. This used to run unconditionally, so saving an
                    # edit to (say) the school name marked the current
                    # enrollment "finished" and created a duplicate — students
                    # accumulated a new enrollment row per edit, and payments
                    # then attached to whichever one came back first.
                    current = student.enrollments.filter(status="active").order_by("-enrollment_date", "-id").first()
                    if current is None or self._enrollment_plan_changed(current, enrollment_form):
                        student.enrollments.filter(status="active").update(status="finished")
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
        context["enrollments"] = self.object.enrollments.all().order_by("-created_at")
        context["payments"] = Payment.objects.filter(student=self.object).order_by("-payment_date")
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
        .prefetch_related("parents")[:10]
    )

    results = []
    for s in students:
        parent = s.parents.all()[0] if s.parents.all() else None
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


def handle_student_form(request):
    """
    Handle student creation and updates
    """

    try:
        # Get form data
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        birth_date = request.POST.get("birth_date")
        email = request.POST.get("email", "").strip()
        school = request.POST.get("school", "").strip()
        group_id = request.POST.get("group")
        allergies = request.POST.get("allergies", "").strip()
        gdpr_signed = request.POST.get("gdpr_signed") == "on"
        active = request.POST.get("active") == "on"
        parent_ids = request.POST.getlist("parents")

        # Validation
        if not first_name or not last_name:
            messages.error(request, "El nombre y apellidos son obligatorios.")
            return redirect("students_list")

        if not birth_date:
            messages.error(request, "La fecha de nacimiento es obligatoria.")
            return redirect("students_list")

        if not group_id:
            messages.error(request, "Debe seleccionar un grupo.")
            return redirect("students_list")

        if not parent_ids:
            messages.error(request, "Debe seleccionar al menos un padre/tutor.")
            return redirect("students_list")

        # Get the group
        try:
            group = Group.objects.get(id=group_id, active=True)
        except Group.DoesNotExist:
            messages.error(request, "El grupo seleccionado no existe.")
            return redirect("students_list")

        # Get parents
        parents = Parent.objects.filter(id__in=parent_ids)
        if len(parents) != len(parent_ids):
            messages.error(request, "Algunos padres seleccionados no existen.")
            return redirect("students_list")

        # Use transaction to ensure data consistency
        with transaction.atomic():
            # Check if this is an update (student_id present) or create
            student_id = request.POST.get("student_id")

            if student_id:  # Update existing student
                try:
                    student = Student.objects.select_related("group").get(id=student_id)
                    old_group = student.group

                    # Update student fields
                    student.first_name = first_name
                    student.last_name = last_name
                    student.birth_date = birth_date
                    student.email = email if email else ""
                    student.school = school if school else ""
                    student.group = group
                    student.allergies = allergies if allergies else ""
                    student.gdpr_signed = gdpr_signed
                    student.active = active

                    student.full_clean()  # Validate the model
                    student.save()

                    if old_group != group:
                        HistoryLog.log(
                            "group_updated",
                            f"Grupo cambiado: {student.full_name} — {old_group.group_name} → {group.group_name}",
                            icon="swap_horiz",
                        )

                    # Update parent relationships
                    student.parents.clear()  # Remove all current relationships
                    student.parents.set(parents)  # Set new relationships

                    messages.success(
                        request,
                        f"Estudiante {student.full_name} actualizado correctamente.",
                    )

                except Student.DoesNotExist:
                    messages.error(request, "El estudiante a actualizar no existe.")
                    return redirect("students_list")

            else:  # Create new student
                student = Student(
                    first_name=first_name,
                    last_name=last_name,
                    birth_date=birth_date,
                    email=email if email else "",
                    school=school if school else "",
                    group=group,
                    allergies=allergies if allergies else "",
                    gdpr_signed=gdpr_signed,
                    active=active,
                )

                student.full_clean()  # Validate the model
                student.save()

                # Add parent relationships
                student.parents.set(parents)

                messages.success(request, f"Estudiante {student.full_name} creado correctamente.")

        return redirect("students_list")

    except ValidationError as e:
        if hasattr(e, "message_dict"):
            for field, errors in e.message_dict.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
        else:
            messages.error(request, f"Error de validación: {e.message}")
        return redirect("students_list")

    except Exception:
        # Never return `str(e)` to the client: on an IntegrityError it names the
        # table and column, on a DataError the column type and length. The
        # ValidationError branch above is the exception to that rule, because
        # ValidationError.messages is text written for humans.
        #
        # And log it — this used to show the operator the only copy of the error
        # and keep nothing, so a recurring failure left no trace at all.
        logger.exception("Unhandled error while processing the student form")
        messages.error(request, "Error al procesar el formulario. Inténtalo de nuevo.")
        return redirect("students_list")


def student_detail(request, student_id):
    """
    API endpoint to get student details for editing
    """
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        student = get_object_or_404(
            Student.objects.select_related("group").prefetch_related("parents"),
            id=student_id,
        )

        # Prepare student data
        student_data = {
            "id": student.id,
            "first_name": student.first_name,
            "last_name": student.last_name,
            "birth_date": student.birth_date.strftime("%Y-%m-%d") if student.birth_date else "",
            "email": student.email,
            "school": student.school,
            "group": student.group.id,
            "allergies": student.allergies,
            "gdpr_signed": student.gdpr_signed,
            "active": student.active,
            "parents": [parent.id for parent in student.parents.all()],
        }

        return JsonResponse(student_data)

    except Exception:
        logger.exception("Error building student payload for student %s", student_id)
        return JsonResponse({"error": "No se pudieron cargar los datos del alumno."}, status=500)


def update_student(request, student_id):
    """
    Maneja la edición de un estudiante:
    - GET: devuelve datos en JSON para rellenar el modal (AJAX).
    - POST: actualiza datos usando handle_student_form y redirige a /students.
    """
    if request.method == "GET":
        student = get_object_or_404(
            Student.objects.select_related("group").prefetch_related("parents"),
            id=student_id,
        )

        data = {
            "id": student.id,
            "first_name": student.first_name,
            "last_name": student.last_name,
            "birth_date": student.birth_date.strftime("%Y-%m-%d") if student.birth_date else "",
            "email": student.email,
            "school": student.school,
            "group": student.group.id if student.group else None,
            "allergies": student.allergies,
            "gdpr_signed": student.gdpr_signed,
            "active": student.active,
            "parents": list(student.parents.values_list("id", flat=True)),
        }
        return JsonResponse(data)

    elif request.method == "POST":
        request.POST = request.POST.copy()
        request.POST["student_id"] = student_id
        return handle_student_form(request)

    return JsonResponse({"error": "Method not allowed"}, status=405)
