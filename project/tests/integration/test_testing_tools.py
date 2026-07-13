"""Tests for core.views.testing_tools — QA-only dashboard + API endpoints.

All views are protected by @qa_access_required, which requires:
- settings.IS_TESTING_ENV == True
- the request is made by a logged-in Teacher (admin or not)

Tests use override_settings + a Teacher-authenticated client to satisfy the gate.
"""

import json
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import reverse

pytestmark = pytest.mark.django_db


QA_SETTINGS = override_settings(IS_TESTING_ENV=True)


@pytest.fixture
def qa_client(client, db):
    """A Django test client authenticated as a logged-in Teacher (QA access)."""
    from students.models import Teacher

    teacher = Teacher.objects.create(
        first_name="QA",
        last_name="Tester",
        email="qa.tester@fiveaday.test",
        phone="600000000",
        active=True,
        admin=True,
    )
    user = teacher.ensure_user(password="qa-pass-123")
    client.force_login(user)
    session = client.session
    session["is_authenticated"] = True
    session["username"] = teacher.first_name
    session.save()
    return client


# ============================================================================
# testing_tools_view (GET)
# ============================================================================


class TestTestingToolsView:
    @QA_SETTINGS
    def test_renders_for_qa_user(self, qa_client):
        response = qa_client.get(reverse("testing_tools"))
        assert response.status_code == 200

    def test_404_for_non_qa_user(self, authenticated_client):
        # By default IS_TESTING_ENV=False in settings_test → 404
        response = authenticated_client.get(reverse("testing_tools"))
        assert response.status_code == 404

    @QA_SETTINGS
    def test_context_contains_expected_keys(self, qa_client):
        response = qa_client.get(reverse("testing_tools"))
        assert response.status_code == 200
        ctx = response.context
        assert "git" in ctx
        assert "qa_config" in ctx
        assert "tasks" in ctx
        assert "app_version" in ctx
        assert "python_version" in ctx

    @QA_SETTINGS
    def test_git_subprocess_failure_is_handled(self, qa_client):
        """If the git subprocess call throws, _git_info returns {} without raising."""
        import subprocess

        with patch("core.views.testing_tools.subprocess.run", side_effect=subprocess.SubprocessError):
            response = qa_client.get(reverse("testing_tools"))
        assert response.status_code == 200
        assert response.context["git"] == {}


# ============================================================================
# api_seed_database (POST)
# ============================================================================


class TestApiSeedDatabase:
    @QA_SETTINGS
    def test_success(self, qa_client):
        with patch("django.core.management.call_command") as mock_cmd:
            response = qa_client.post(
                reverse("api_seed_database"),
                data=json.dumps({"reset": False}),
                content_type="application/json",
            )
        assert response.status_code == 200
        assert response.json()["success"] is True
        mock_cmd.assert_called_once()

    @QA_SETTINGS
    def test_success_with_reset(self, qa_client):
        with patch("django.core.management.call_command") as mock_cmd:
            response = qa_client.post(
                reverse("api_seed_database"),
                data=json.dumps({"reset": True}),
                content_type="application/json",
            )
        assert response.status_code == 200
        assert response.json()["success"] is True
        # reset=True should be forwarded as kwarg
        call_kwargs = mock_cmd.call_args.kwargs
        assert call_kwargs.get("reset") is True

    @QA_SETTINGS
    def test_command_error_returns_500(self, qa_client):
        with patch("django.core.management.call_command", side_effect=RuntimeError("boom")):
            response = qa_client.post(
                reverse("api_seed_database"),
                data=json.dumps({"reset": False}),
                content_type="application/json",
            )
        assert response.status_code == 500
        assert response.json()["success"] is False

    def test_404_for_non_qa_user(self, authenticated_client):
        response = authenticated_client.post(
            reverse("api_seed_database"),
            data=json.dumps({"reset": False}),
            content_type="application/json",
        )
        assert response.status_code == 404


# ============================================================================
# api_create_backlog_task (POST)
# ============================================================================


class TestApiCreateBacklogTask:
    @QA_SETTINGS
    def test_create_success(self, qa_client):
        with patch("core.views.testing_tools.send_mail"):
            response = qa_client.post(
                reverse("api_create_backlog_task"),
                data=json.dumps({"title": "Fix login", "description": "Some detail", "priority": "high"}),
                content_type="application/json",
            )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["task"]["title"] == "Fix login"
        assert data["task"]["priority"] == "high"

    @QA_SETTINGS
    def test_missing_title_returns_400(self, qa_client):
        response = qa_client.post(
            reverse("api_create_backlog_task"),
            data=json.dumps({"title": "", "description": "", "priority": "low"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    @QA_SETTINGS
    def test_invalid_priority_returns_400(self, qa_client):
        response = qa_client.post(
            reverse("api_create_backlog_task"),
            data=json.dumps({"title": "Ok title", "description": "", "priority": "urgent"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    @QA_SETTINGS
    def test_invalid_json_returns_400(self, qa_client):
        response = qa_client.post(
            reverse("api_create_backlog_task"),
            data="not-json",
            content_type="application/json",
        )
        assert response.status_code == 400

    @override_settings(IS_TESTING_ENV=True, SUPPORT_EMAIL="sup@test.com")
    def test_email_is_sent_when_support_email_configured(self, qa_client):
        with patch("core.views.testing_tools.send_mail") as mock_mail:
            qa_client.post(
                reverse("api_create_backlog_task"),
                data=json.dumps({"title": "Issue", "description": "", "priority": "medium"}),
                content_type="application/json",
            )
        mock_mail.assert_called_once()

    @override_settings(IS_TESTING_ENV=True, SUPPORT_EMAIL="sup@test.com")
    def test_email_failure_is_swallowed(self, qa_client):
        """send_mail throwing does not break the task creation."""
        with patch("core.views.testing_tools.send_mail", side_effect=RuntimeError("smtp down")):
            response = qa_client.post(
                reverse("api_create_backlog_task"),
                data=json.dumps({"title": "Issue", "description": "", "priority": "medium"}),
                content_type="application/json",
            )
        assert response.status_code == 200
        assert response.json()["success"] is True


# ============================================================================
# api_update_backlog_task (POST)
# ============================================================================


class TestApiUpdateBacklogTask:
    @QA_SETTINGS
    def test_update_status_success(self, qa_client):
        from core.models import BacklogTask

        task = BacklogTask.objects.create(title="T", description="", priority="low", created_by="u")
        response = qa_client.post(
            reverse("api_update_backlog_task", kwargs={"task_id": task.id}),
            data=json.dumps({"status": "done"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
        task.refresh_from_db()
        assert task.status == "done"

    @QA_SETTINGS
    def test_invalid_status_returns_400(self, qa_client):
        from core.models import BacklogTask

        task = BacklogTask.objects.create(title="T", description="", priority="low", created_by="u")
        response = qa_client.post(
            reverse("api_update_backlog_task", kwargs={"task_id": task.id}),
            data=json.dumps({"status": "nope"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    @QA_SETTINGS
    def test_nonexistent_task_returns_404(self, qa_client):
        response = qa_client.post(
            reverse("api_update_backlog_task", kwargs={"task_id": 99999}),
            data=json.dumps({"status": "done"}),
            content_type="application/json",
        )
        assert response.status_code == 404


# ============================================================================
# api_toggle_error_email (POST)
# ============================================================================


class TestApiToggleErrorEmail:
    @QA_SETTINGS
    def test_toggle_on(self, qa_client):
        response = qa_client.post(
            reverse("api_toggle_error_email"),
            data=json.dumps({"enabled": True}),
            content_type="application/json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["enabled"] is True

    @QA_SETTINGS
    def test_toggle_off(self, qa_client):
        response = qa_client.post(
            reverse("api_toggle_error_email"),
            data=json.dumps({"enabled": False}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is False

    @QA_SETTINGS
    def test_bad_json_returns_500(self, qa_client):
        response = qa_client.post(
            reverse("api_toggle_error_email"),
            data="not json",
            content_type="application/json",
        )
        assert response.status_code == 500
