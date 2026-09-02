import json
import logging
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from billing import constants
from billing.models import Enrollment, SiteConfiguration, current_academic_year, relevant_academic_years
from core.models import HistoryLog
from students.models import Group, Parent, Student, Teacher

logger = logging.getLogger(__name__)


def gestion_view(request):
    """
    Vista principal de gestión con configuración de precios, profesores y grupos.
    """
    from core.views.waiting_list import group_capacity_summary

    config = SiteConfiguration.get_config()
    teachers = Teacher.objects.filter(active=True).order_by("first_name", "last_name")
    # One annotated query for every group's occupancy. The template read
    # `group.enrolled_count` twice plus `group.is_full` per row, and all three are
    # uncached `.count()` properties — three queries per group (40 for 13 groups).
    # Same helper `waiting_list_view` and the dashboard use, so the three pages
    # cannot disagree about whether a group is full.
    groups = group_capacity_summary()

    context = {
        "config": config,
        "teachers": teachers,
        "groups": groups,
    }
    return render(request, "management.html", context)


@require_http_methods(["POST"])
def update_site_config(request):
    """API para actualizar la configuración de precios del sitio."""
    try:
        data = json.loads(request.body)
        config = SiteConfiguration.get_config()

        if "children_enrollment_fee" in data:
            config.children_enrollment_fee = Decimal(str(data["children_enrollment_fee"]))
        if "adult_enrollment_fee" in data:
            config.adult_enrollment_fee = Decimal(str(data["adult_enrollment_fee"]))

        if "full_time_monthly_fee" in data:
            config.full_time_monthly_fee = Decimal(str(data["full_time_monthly_fee"]))
        if "part_time_monthly_fee" in data:
            config.part_time_monthly_fee = Decimal(str(data["part_time_monthly_fee"]))
        if "adult_group_monthly_fee" in data:
            config.adult_group_monthly_fee = Decimal(str(data["adult_group_monthly_fee"]))

        for field in [
            "language_cheque_discount",
            "quarterly_enrollment_discount",
            "old_student_discount",
            "june_discount",
            "full_year_bonus",
            "sibling_discount",
            "half_month_discount",
            "one_week_discount",
            "three_week_discount",
            # v1.13 — returning-student enrollment discount (flat €)
            "returning_student_enrollment_discount",
        ]:
            if field in data:
                setattr(config, field, Decimal(str(data[field])))

        # Model.save() skips validators, so a negative fee (or a percentage over
        # 100) used to persist straight through and quietly break every price.
        config.full_clean()
        config.save()

        HistoryLog.log("config_updated", "Precios o descuentos actualizados", icon="tune")

        return JsonResponse({"success": True, "message": "Configuración actualizada correctamente"})
    except ValidationError as e:
        # Django's own field messages — written to be shown to a user.
        return JsonResponse(
            {"success": False, "message": " ".join(m for msgs in e.message_dict.values() for m in msgs)},
            status=400,
        )
    except Exception:
        logger.exception("Error updating site configuration")
        return JsonResponse(
            {"success": False, "message": "No se pudo actualizar la configuración. Revisa los valores."},
            status=400,
        )


@require_http_methods(["POST"])
def create_teacher(request):
    """API para crear un nuevo profesor."""
    try:
        data = json.loads(request.body)

        required_fields = ["first_name", "last_name", "email"]
        for field in required_fields:
            if not data.get(field):
                return JsonResponse(
                    {"success": False, "message": f"El campo {field} es requerido"},
                    status=400,
                )

        if Teacher.objects.filter(email=data["email"]).exists():
            return JsonResponse(
                {"success": False, "message": "Ya existe un profesor con ese email"},
                status=400,
            )

        # Teachers created here are ALWAYS non-admin. Only the seeded teachers
        # (TEACHER_SEED_*) and the superuser/admin profile are admins; an
        # existing admin can promote a teacher afterwards via /admin/.
        teacher = Teacher(
            first_name=data["first_name"],
            last_name=data["last_name"],
            email=data["email"],
            phone=data.get("phone", ""),
            active=True,
            admin=False,
        )
        # `objects.create()` runs no validators, so "notanemail" persisted
        # happily into an EmailField — and the account it produces is
        # unreachable: `ensure_user()` mirrors the address onto the auth.User,
        # and activation happens over `/password-reset/`, which emails it.
        teacher.full_clean()
        teacher.save()

        # Create the linked auth.User with an unusable password. Without this a
        # teacher created here had no login identity at all: they could not sign
        # in AND `/password-reset/` silently sent nothing (no User matched the
        # address), so the account was unreachable. They now activate the same
        # way seeded teachers do — via the password-reset flow.
        teacher.ensure_user()

        HistoryLog.log("teacher_created", f"Profesor creado: {teacher.full_name}", icon="person_add")

        return JsonResponse(
            {
                "success": True,
                "message": "Profesor creado. Debe activar su cuenta desde “¿Olvidaste tu contraseña?”.",
                "teacher": {
                    "id": teacher.id,
                    "full_name": teacher.full_name,
                    "email": teacher.email,
                },
            }
        )
    except ValidationError as e:
        # Django's own field messages — written to be shown to a user, unlike
        # the exception text the catch-all below deliberately swallows.
        return JsonResponse(
            {"success": False, "message": " ".join(m for msgs in e.message_dict.values() for m in msgs)},
            status=400,
        )
    except Exception:
        logger.exception("Error creating teacher")
        return JsonResponse(
            {"success": False, "message": "No se pudo crear el profesor. Revisa los datos."},
            status=400,
        )


@require_http_methods(["POST"])
def create_group(request):
    """API para crear un nuevo grupo."""
    try:
        data = json.loads(request.body)

        if not data.get("group_name"):
            return JsonResponse(
                {"success": False, "message": "El nombre del grupo es requerido"},
                status=400,
            )

        if not data.get("teacher_id"):
            return JsonResponse({"success": False, "message": "El profesor es requerido"}, status=400)

        if Group.objects.filter(group_name=data["group_name"]).exists():
            return JsonResponse(
                {"success": False, "message": "Ya existe un grupo con ese nombre"},
                status=400,
            )

        try:
            teacher = Teacher.objects.get(id=data["teacher_id"], active=True)
        except Teacher.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": "El profesor seleccionado no existe"},
                status=400,
            )

        # Cupo máximo: chosen at creation only (there is no edit UI). Defaults to
        # the model default (8) when the field is left empty; 0 means "no cap".
        raw_max = data.get("max_students", "")
        if raw_max in (None, ""):
            max_students = Group._meta.get_field("max_students").default
        else:
            try:
                max_students = int(raw_max)
            except (TypeError, ValueError):
                return JsonResponse(
                    {"success": False, "message": "El cupo máximo debe ser un número entero"},
                    status=400,
                )
            if max_students < 0:
                return JsonResponse(
                    {"success": False, "message": "El cupo máximo no puede ser negativo"},
                    status=400,
                )

        group = Group.objects.create(
            group_name=data["group_name"],
            color=data.get("color", "#6366f1"),
            teacher=teacher,
            max_students=max_students,
            active=True,
        )

        HistoryLog.log(
            "group_created",
            f"Grupo creado: {group.group_name} (Prof. {teacher.full_name}) — cupo {group.max_students or 'sin límite'}",
            icon="group_add",
        )

        return JsonResponse(
            {
                "success": True,
                "message": "Grupo creado correctamente",
                "group": {
                    "id": group.id,
                    "group_name": group.group_name,
                    "teacher_name": teacher.full_name,
                    "max_students": group.max_students,
                },
            }
        )
    except Exception:
        logger.exception("Error creating group")
        return JsonResponse(
            {"success": False, "message": "No se pudo crear el grupo. Revisa los datos."},
            status=400,
        )


def api_get_teachers(request):
    """API para obtener lista de profesores activos (para select de grupos)."""
    teachers = Teacher.objects.filter(active=True).order_by("first_name", "last_name")
    data = [{"id": t.id, "full_name": t.full_name, "email": t.email} for t in teachers]
    return JsonResponse({"teachers": data})


@require_http_methods(["POST"])
def update_enrollment_modality(request, student_id):
    """
    AJAX endpoint to change a student's payment modality (monthly/quarterly).
    Expects JSON body: {"payment_modality": "monthly"|"quarterly"}
    """
    try:
        student = get_object_or_404(Student, id=student_id)
        data = json.loads(request.body)
        modality = data.get("payment_modality")

        if modality not in dict(constants.PAYMENT_MODALITY_CHOICES):
            return JsonResponse(
                {"success": False, "error": "Modalidad de pago no válida"},
                status=400,
            )

        enrollment = student.enrollments.filter(status="active").first()
        if not enrollment:
            return JsonResponse(
                {"success": False, "error": "No tiene matrícula activa"},
                status=404,
            )

        enrollment.payment_modality = modality
        enrollment.save()

        return JsonResponse(
            {
                "success": True,
                "message": f"Modalidad cambiada a {enrollment.get_payment_modality_display()}.",
                "payment_modality": modality,
                "payment_modality_display": enrollment.get_payment_modality_display(),
            }
        )

    except Exception:
        logger.exception("Error changing payment modality")
        return JsonResponse(
            {"success": False, "error": "No se pudo cambiar la modalidad de pago."},
            status=500,
        )


@require_http_methods(["GET"])
def language_cheque_students(request):
    """
    API endpoint to fetch students with active language cheque (cheque idioma).
    """
    academic_years = relevant_academic_years()
    enrollments = (
        Enrollment.objects.filter(
            status="active",
            academic_year__in=academic_years,
            has_language_cheque=True,
        )
        .select_related("student", "student__group")
        # Ordered so the parent shown is the same one `.first()` used to return
        # (an unordered `first()` sorts by pk) — see generate_payments.py.
        .prefetch_related(Prefetch("student__parents", queryset=Parent.objects.order_by("id")))
    )

    students_data = []
    for enrollment in enrollments:
        s = enrollment.student
        # `.first()` re-queries and discards the prefetch above — see the note in
        # billing/management/commands/generate_payments.py.
        parent = next(iter(s.parents.all()), None)
        students_data.append(
            {
                "id": s.id,
                "full_name": s.full_name,
                # birth_date is optional (waiting-list entries may not have one).
                "birth_date": s.birth_date.strftime("%Y-%m-%d") if s.birth_date else "",
                "group": s.group.group_name if s.group else "",
                "parent_name": parent.full_name if parent else "",
                "parent_dni": parent.dni if parent else "",
                "schedule_type": enrollment.get_schedule_type_display(),
            }
        )

    return JsonResponse(
        {
            "success": True,
            "academic_year": current_academic_year(),
            "count": len(students_data),
            "students": students_data,
        }
    )
