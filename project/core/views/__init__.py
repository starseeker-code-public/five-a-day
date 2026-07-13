"""
Core views package — re-exports all views for URL routing compatibility.
"""

# Auth
# App forms (email tools)
from core.views.app_forms import (
    apps_view,
    birthday_form,
    enrollment_form,
    fun_friday_form,
    monthly_report_form,
    newsletter_form,
    payment_reminder_form,
    receipts_form,
    tax_certificate_form,
    vacation_closure_form,
    welcome_form,
)
from core.views.auth import (
    google_oauth_callback,
    google_oauth_redirect,
    login_view,
    logout_view,
)

# Dashboard
from core.views.dashboard import all_info, home

# Errors & health
from core.views.errors import (
    handler400,
    handler403,
    handler404,
    handler405,
    handler500,
    health_check,
    test_error_400,
    test_error_403,
    test_error_404,
    test_error_405,
    test_error_500,
)

# Expenses (v1.5)
from core.views.expenses import (
    create_expense,
    delete_expense,
    expenses_list,
)

# Fun Friday attendance
from core.views.fun_friday_attendance import (
    add_fun_friday_attendance,
    remove_fun_friday_attendance,
    toggle_fun_friday_this_week,
)

# Management & enrollment API
from core.views.management import (
    api_get_teachers,
    create_group,
    create_teacher,
    gestion_view,
    language_cheque_students,
    update_enrollment_modality,
    update_site_config,
)

# Parent portal (v1.9)
from core.views.parent_portal import (
    parent_portal_dashboard,
    parent_portal_login,
    parent_portal_logout,
    parent_portal_payments,
    parent_portal_receipt,
    parent_portal_tax_certificate,
    parent_portal_verify,
)

# Parents
from core.views.parents import ParentCreateView
from core.views.password_reset import (
    BrandedPasswordResetCompleteView,
    BrandedPasswordResetConfirmView,
    BrandedPasswordResetDoneView,
    BrandedPasswordResetView,
)

# Payments
from core.views.payments import (
    create_payment,
    deactivate_payment,
    delete_payment,
    export_database_excel,
    export_payments,
    get_payment_details,
    parse_date_value,
    payment_detail,
    payment_detail_view,
    payment_receipt_pdf,
    payment_statistics,
    payments_list,
    quick_complete_payment,
    search_parents,
    search_payments,
    update_payment,
    validate_student_parent,
)

# PWA (v1.12)
from core.views.pwa import service_worker, web_manifest

# Reports & analytics (v1.7)
from core.views.reports import reports_pdf, reports_view

# Schedule
from core.views.schedule import fun_friday_view, save_schedule_slot, schedule_view

# Sheets export (v1.2)
from core.views.sheets import export_to_sheets

# Stripe (v1.11)
from core.views.stripe_views import create_checkout_link, stripe_webhook

# Students
from core.views.students import (
    StudentCreateView,
    StudentDetailView,
    StudentListView,
    StudentUpdateView,
    get_ff_student_ids,
    get_last_friday,
    get_next_friday,
    handle_student_form,
    search_students,
    student_detail,
    update_student,
)

# Support
from core.views.support import submit_support_ticket

# Testing tools (QA)
from core.views.testing_tools import (
    api_create_backlog_task,
    api_mark_ready,
    api_seed_database,
    api_toggle_error_email,
    api_update_backlog_task,
    testing_tools_view,
)

# Todos & history
from core.views.todos import complete_todo, create_todo, history_list

# Two-factor authentication (v1.13)
from core.views.two_factor import (
    two_factor_manage,
    two_factor_setup,
    two_factor_verify,
)

# Waiting list (v1.1)
from core.views.waiting_list import (
    add_to_waiting_list,
    assign_from_waiting_list,
    group_capacity_summary,
    notify_capacity_freed,
    waiting_list_view,
)
