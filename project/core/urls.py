from django.urls import path

from core.views import (
    BrandedPasswordResetCompleteView,
    BrandedPasswordResetConfirmView,
    BrandedPasswordResetDoneView,
    BrandedPasswordResetView,
    all_info,
    api_create_backlog_task,
    api_mark_ready,
    api_seed_database,
    api_toggle_error_email,
    api_update_backlog_task,
    complete_todo,
    # Stripe (v1.11)
    create_checkout_link,
    # Todos
    create_todo,
    export_backlog_tasks,
    # Google Sheets export (v1.2)
    export_to_sheets,
    fun_friday_view,
    google_oauth_callback,
    google_oauth_redirect,
    # History
    history_list,
    # Dashboard
    home,
    # Auth
    login_view,
    logout_view,
    # Parent portal (v1.9)
    parent_portal_dashboard,
    parent_portal_login,
    parent_portal_logout,
    parent_portal_payments,
    parent_portal_receipt,
    parent_portal_tax_certificate,
    parent_portal_verify,
    # Reports & analytics (v1.7)
    reports_pdf,
    reports_view,
    save_schedule_slot,
    # Schedule
    schedule_view,
    # PWA (v1.12)
    service_worker,
    # Stripe webhook (v1.11) — CSRF-exempt
    stripe_webhook,
    # Support
    submit_support_ticket,
    # Error test pages
    test_error_400,
    test_error_403,
    test_error_404,
    test_error_405,
    test_error_500,
    # Testing tools
    testing_tools_view,
    # Two-factor authentication (v1.13)
    two_factor_manage,
    two_factor_setup,
    two_factor_verify,
    web_manifest,
)

urlpatterns = [
    # Authentication
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("auth/google/", google_oauth_redirect, name="google_oauth_redirect"),
    path("auth/google/callback/", google_oauth_callback, name="google_oauth_callback"),
    # Password reset (public — accessible without being logged in)
    path("password-reset/", BrandedPasswordResetView.as_view(), name="password_reset"),
    path("password-reset/sent/", BrandedPasswordResetDoneView.as_view(), name="password_reset_done"),
    path(
        "password-reset/confirm/<uidb64>/<token>/",
        BrandedPasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/complete/",
        BrandedPasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    # PWA (v1.12) — must be at origin root to control the whole scope
    path("manifest.webmanifest", web_manifest, name="web_manifest"),
    path("sw.js", service_worker, name="service_worker"),
    # Dashboard
    path("", home, name="home"),
    path("database/", all_info, name="all_info"),
    # Schedule
    path("schedule/", schedule_view, name="schedule_view"),
    path("api/schedule/slot/save/", save_schedule_slot, name="save_schedule_slot"),
    path("fun-friday/", fun_friday_view, name="fun_friday_view"),
    # Todos
    path("api/todos/create/", create_todo, name="create_todo"),
    path("api/todos/<int:todo_id>/complete/", complete_todo, name="complete_todo"),
    # History
    path("api/history/", history_list, name="history_list"),
    # Support
    path("api/support/submit/", submit_support_ticket, name="submit_support_ticket"),
    # Google Sheets export (v1.2)
    path("api/sheets/export/", export_to_sheets, name="export_to_sheets"),
    # Reports & analytics (v1.7)
    path("reports/", reports_view, name="reports_view"),
    path("reports/download.pdf", reports_pdf, name="reports_pdf"),
    # Two-factor authentication (v1.13)
    path("two-factor/setup/", two_factor_setup, name="two_factor_setup"),
    path("two-factor/manage/", two_factor_manage, name="two_factor_manage"),
    path("two-factor/verify/", two_factor_verify, name="two_factor_verify"),
    # Parent portal (v1.9) — magic-link auth, session separate from admin
    path("parent/login/", parent_portal_login, name="parent_portal_login"),
    path("parent/login/<str:token>/", parent_portal_verify, name="parent_portal_verify"),
    path("parent/logout/", parent_portal_logout, name="parent_portal_logout"),
    path("parent/", parent_portal_dashboard, name="parent_portal_dashboard"),
    path("parent/payments/", parent_portal_payments, name="parent_portal_payments"),
    path("parent/payments/<int:payment_id>/receipt.pdf", parent_portal_receipt, name="parent_portal_receipt"),
    path("parent/tax-certificate.pdf", parent_portal_tax_certificate, name="parent_portal_tax_certificate"),
    path(
        "parent/payments/<int:payment_id>/pay-online/",
        create_checkout_link,
        name="stripe_create_checkout_link",
    ),
    # Stripe webhook (v1.11) — CSRF-exempt, called by Stripe's servers
    path("api/stripe/webhook/", stripe_webhook, name="stripe_webhook"),
    # Testing tools
    path("testing/", testing_tools_view, name="testing_tools"),
    path("api/testing/seed/", api_seed_database, name="api_seed_database"),
    path("api/testing/backlog/create/", api_create_backlog_task, name="api_create_backlog_task"),
    path("api/testing/backlog/<int:task_id>/update/", api_update_backlog_task, name="api_update_backlog_task"),
    path("api/testing/backlog/export/", export_backlog_tasks, name="export_backlog_tasks"),
    path("api/testing/error-email/toggle/", api_toggle_error_email, name="api_toggle_error_email"),
    path("api/testing/ready/", api_mark_ready, name="api_mark_ready"),
    # Error test pages
    path("400/", test_error_400, name="test_error_400"),
    path("403/", test_error_403, name="test_error_403"),
    path("404/", test_error_404, name="test_error_404"),
    path("405/", test_error_405, name="test_error_405"),
    path("500/", test_error_500, name="test_error_500"),
]
