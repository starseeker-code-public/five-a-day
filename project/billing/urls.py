from django.urls import path

from core.views import (
    api_get_teachers,
    create_expense,
    create_group,
    create_payment,
    create_teacher,
    deactivate_payment,
    delete_expense,
    delete_payment,
    # Expenses (v1.5)
    expenses_list,
    export_database_excel,
    export_payments,
    # Management
    gestion_view,
    get_payment_details,
    language_cheque_students,
    payment_detail_view,
    payment_receipt_pdf,
    payment_statistics,
    # Payments
    payments_list,
    quick_complete_payment,
    # Search/API
    search_payments,
    student_payments_pdf,
    # Enrollment API
    update_enrollment_modality,
    update_payment,
    update_site_config,
)

urlpatterns = [
    # ============================================================================
    # PAYMENT MANAGEMENT - Gestión de Pagos
    # ============================================================================
    path("payments/", payments_list, name="payments_list"),
    path("payments/create/", create_payment, name="create_payment"),
    path("payments/<int:payment_id>/", payment_detail_view, name="payment_detail_view"),
    path("payments/<int:payment_id>/receipt.pdf", payment_receipt_pdf, name="payment_receipt_pdf"),
    path("students/<int:student_id>/payments.pdf", student_payments_pdf, name="student_payments_pdf"),
    path("payments/<int:payment_id>/update/", update_payment, name="update_payment"),
    path("payments/<int:payment_id>/delete/", delete_payment, name="delete_payment"),
    path(
        "payments/<int:payment_id>/deactivate/",
        deactivate_payment,
        name="deactivate_payment",
    ),
    path(
        "api/payments/<int:payment_id>/quick-complete/",
        quick_complete_payment,
        name="quick_complete_payment",
    ),
    # ============================================================================
    # ENROLLMENT API
    # ============================================================================
    path(
        "api/students/<int:student_id>/enrollment/modality/",
        update_enrollment_modality,
        name="update_enrollment_modality",
    ),
    path(
        "api/students/language-cheque/",
        language_cheque_students,
        name="language_cheque_students",
    ),
    # ============================================================================
    # API ENDPOINTS - Search and Statistics
    # ============================================================================
    path("api/search/payments/", search_payments, name="search_payments"),
    path(
        "api/payments/<int:payment_id>/details/",
        get_payment_details,
        name="get_payment_details",
    ),
    path("api/payments/statistics/", payment_statistics, name="payment_statistics"),
    path("payments/export/", export_payments, name="export_payments"),
    path("database/export/", export_database_excel, name="export_database_excel"),
    # ============================================================================
    # GESTIÓN - Configuración del Sitio, Profesores y Grupos
    # ============================================================================
    path("management/", gestion_view, name="management"),
    path("api/config/update/", update_site_config, name="update_site_config"),
    path("api/teachers/", api_get_teachers, name="api_get_teachers"),
    path("api/teachers/create/", create_teacher, name="create_teacher"),
    path("api/groups/create/", create_group, name="create_group"),
    # ============================================================================
    # EXPENSES (v1.5)
    # ============================================================================
    path("expenses/", expenses_list, name="expenses_list"),
    path("expenses/create/", create_expense, name="create_expense"),
    path("expenses/<int:expense_id>/delete/", delete_expense, name="delete_expense"),
]
