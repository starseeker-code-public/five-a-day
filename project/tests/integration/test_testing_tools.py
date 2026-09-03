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
    # Forced off — the local .env may legitimately set GCP_BILLING_EXPORT_TABLE.
    @override_settings(GCP_BILLING_EXPORT_TABLE="")
    def test_gcp_costs_in_context(self, qa_client):
        """Unconfigured — placeholders, no network, no 500."""
        response = qa_client.get(reverse("testing_tools"))
        assert response.status_code == 200
        card = response.context["gcp_costs"]
        assert card["previous"] is None
        assert card["current"] is None
        assert card["previous_label"] and card["current_label"]

    @QA_SETTINGS
    @override_settings(GCP_BILLING_EXPORT_TABLE="")
    def test_gcp_costs_show_archived_previous_month(self, qa_client):
        """A finished month reads from its archived Expense row, never live."""
        from datetime import date
        from decimal import Decimal

        from billing.models import Expense
        from billing.services.gcp_cost_service import GCP_EXPENSE_DESCRIPTION, previous_month

        prev_year, prev_month = previous_month()
        Expense.objects.create(
            description=GCP_EXPENSE_DESCRIPTION,
            category="software",
            amount=Decimal("10.15"),
            expense_date=date(prev_year, prev_month, 1),
        )
        response = qa_client.get(reverse("testing_tools"))
        assert response.context["gcp_costs"]["previous"] == Decimal("10.15")
        assert b"Gastos GCP" in response.content

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
        # Two commands: the QA dataset, then the demo parent — without the
        # latter there is no way to open /parent/login/ on the VM, because the
        # real flow needs a mailbox to read the invitation from.
        commands = [call.args[0] for call in mock_cmd.call_args_list]
        assert commands == ["seed_testdata", "seed_demo_parents"], (
            "the demo parent must be seeded AFTER seed_testdata, whose --reset wipes every Parent"
        )

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
        # reset=True should be forwarded as a kwarg — to seed_testdata, which is
        # the command that understands it.
        seed_call = next(call for call in mock_cmd.call_args_list if call.args[0] == "seed_testdata")
        assert seed_call.kwargs.get("reset") is True

    @QA_SETTINGS
    def test_a_failing_demo_parent_seed_does_not_fail_the_qa_seed(self, qa_client):
        """The QA dataset is the point; the demo parent is a bonus."""

        def _fail_on_demo(name, *args, **kwargs):
            if name == "seed_demo_parents":
                raise RuntimeError("boom")

        with patch("django.core.management.call_command", side_effect=_fail_on_demo):
            response = qa_client.post(
                reverse("api_seed_database"),
                data=json.dumps({"reset": False}),
                content_type="application/json",
            )

        assert response.status_code == 200
        assert response.json()["success"] is True

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
        from django.core import mail

        qa_client.post(
            reverse("api_create_backlog_task"),
            data=json.dumps({"title": "Issue", "description": "", "priority": "medium"}),
            content_type="application/json",
        )
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["sup@test.com"]

    @override_settings(IS_TESTING_ENV=True, SUPPORT_EMAIL="sup@test.com")
    def test_screenshot_is_attached_to_email_not_stored(self, qa_client):
        from django.core import mail
        from django.core.files.uploadedfile import SimpleUploadedFile

        img = SimpleUploadedFile("shot.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, content_type="image/png")
        # multipart POST (form-data), not JSON — this is how a screenshot rides along
        qa_client.post(
            reverse("api_create_backlog_task"),
            data={"title": "With shot", "description": "", "priority": "low", "screenshot": img},
        )
        assert len(mail.outbox) == 1
        assert len(mail.outbox[0].attachments) == 1
        assert mail.outbox[0].attachments[0][0] == "shot.png"

    @override_settings(IS_TESTING_ENV=True, SUPPORT_EMAIL="sup@test.com")
    def test_email_failure_is_swallowed(self, qa_client):
        """An email send failure does not break task creation."""
        with patch("django.core.mail.EmailMessage.send", side_effect=RuntimeError("smtp down")):
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


# ============================================================================
# api_update_backlog_task — QA verification tick (v1.17.5)
# ============================================================================


class TestBacklogQaVerificationTick:
    """The shaded tick beside the priority badge, which the tester turns green.

    Deliberately separate from `status="done"`: that is the developer saying
    "shipped" and it emails the admin teachers. This is QA saying "verified".
    """

    def _task(self):
        from core.models import BacklogTask

        return BacklogTask.objects.create(title="Revisar el alta", priority="high", created_by="qa")

    def test_defaults_to_unverified(self):
        assert self._task().verified is False

    @QA_SETTINGS
    def test_toggle_on(self, qa_client):
        task = self._task()

        response = qa_client.post(
            reverse("api_update_backlog_task", kwargs={"task_id": task.id}),
            data=json.dumps({"verified": True}),
            content_type="application/json",
        )

        assert response.json() == {"success": True, "verified": True}
        task.refresh_from_db()
        assert task.verified is True

    @QA_SETTINGS
    def test_toggle_off(self, qa_client):
        task = self._task()
        task.verified = True
        task.save()

        qa_client.post(
            reverse("api_update_backlog_task", kwargs={"task_id": task.id}),
            data=json.dumps({"verified": False}),
            content_type="application/json",
        )

        task.refresh_from_db()
        assert task.verified is False

    @QA_SETTINGS
    def test_verifying_does_not_touch_status_or_send_the_done_email(self, qa_client, mailoutbox):
        task = self._task()

        qa_client.post(
            reverse("api_update_backlog_task", kwargs={"task_id": task.id}),
            data=json.dumps({"verified": True}),
            content_type="application/json",
        )

        task.refresh_from_db()
        assert task.status == "open"
        assert mailoutbox == []

    @QA_SETTINGS
    def test_marking_done_still_works_alongside_it(self, qa_client):
        task = self._task()

        qa_client.post(
            reverse("api_update_backlog_task", kwargs={"task_id": task.id}),
            data=json.dumps({"status": "done"}),
            content_type="application/json",
        )

        task.refresh_from_db()
        assert task.status == "done"
        # Verification is the tester's own flag and is untouched by a status change.
        assert task.verified is False


class TestBacklogOrdering:
    """Open work first, done at the bottom.

    The model orders by `-created_at` alone, so a task marked done kept its slot
    among the live tickets — and with the dashboard capped at 50 it pushed real
    work off the page.
    """

    @staticmethod
    def _make(title, status, days_old):
        from datetime import timedelta

        from django.utils import timezone

        from core.models import BacklogTask

        task = BacklogTask.objects.create(title=title, status=status)
        # created_at is auto_now_add, so it has to be rewritten after the insert.
        BacklogTask.objects.filter(pk=task.pk).update(created_at=timezone.now() - timedelta(days=days_old))
        return task

    @QA_SETTINGS
    def test_dashboard_lists_unfinished_tasks_first(self, qa_client):
        recent_done = self._make("Hecha ayer", "done", 1)
        old_open = self._make("Abierta hace un mes", "open", 30)
        old_in_progress = self._make("En curso hace un mes", "in_progress", 29)

        response = qa_client.get(reverse("testing_tools"))
        ordered = [t.id for t in response.context["tasks"]]

        assert ordered.index(old_in_progress.id) < ordered.index(recent_done.id)
        assert ordered.index(old_open.id) < ordered.index(recent_done.id)
        # Newest first still holds within the unfinished half.
        assert ordered.index(old_in_progress.id) < ordered.index(old_open.id)

    @QA_SETTINGS
    def test_full_export_uses_the_same_order(self, qa_client):
        done = self._make("Hecha ayer", "done", 1)
        still_open = self._make("Abierta hace un mes", "open", 30)

        response = qa_client.get(reverse("export_backlog_tasks"), {"format": "json", "scope": "all"})
        ids = [row["id"] for row in json.loads(response.content)["tasks"]]
        assert ids.index(still_open.id) < ids.index(done.id)


# ============================================================================
# api_mark_ready — the QA sign-off that unlocks the production deploy
# ============================================================================


class TestApiMarkReady:
    """The '¿Listo para desplegar?' button: emails support, sets
    QAConfiguration.ready_for_prod (the flag deploy-production.yml's preflight
    reads through /health/?deep=1) AND fires the repository_dispatch that arms
    the production workflow immediately. The flag is set only when the email
    actually went out, so success always means both happened; the dispatch is
    fail-soft, so its outcome never changes the response status."""

    @override_settings(IS_TESTING_ENV=True, SUPPORT_EMAIL="sup@test.com")
    def test_success_sends_email_opens_the_gate_and_arms_the_deploy(self, qa_client, mailoutbox):
        from core.models import QAConfiguration

        assert QAConfiguration.get_config().ready_for_prod is False

        with patch("core.views.testing_tools.notify_github_qa_signoff", return_value=True) as notify:
            response = qa_client.post(reverse("api_mark_ready"), data="{}", content_type="application/json")

        body = response.json()
        assert response.status_code == 200
        assert body["success"] is True
        assert body["ready_for_prod"] is True
        assert body["deploy_dispatched"] is True
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == ["sup@test.com"]
        assert QAConfiguration.get_config().ready_for_prod is True
        notify.assert_called_once_with()

    @override_settings(IS_TESTING_ENV=True, SUPPORT_EMAIL="sup@test.com")
    def test_dispatch_failure_never_fails_the_signoff(self, qa_client, mailoutbox):
        """A missing token or an unreachable GitHub API must not break the
        button: the flag is the source of truth and the nightly workflow_run
        re-trigger remains the fallback arming path."""
        from core.models import QAConfiguration

        with patch("core.views.testing_tools.notify_github_qa_signoff", return_value=False):
            response = qa_client.post(reverse("api_mark_ready"), data="{}", content_type="application/json")

        body = response.json()
        assert response.status_code == 200
        assert body["success"] is True
        assert body["deploy_dispatched"] is False
        assert QAConfiguration.get_config().ready_for_prod is True

    @override_settings(IS_TESTING_ENV=True, SUPPORT_EMAIL="sup@test.com")
    def test_email_failure_keeps_the_gate_closed(self, qa_client):
        from core.models import QAConfiguration

        with (
            patch("core.views.testing_tools.send_mail", side_effect=RuntimeError("smtp down")),
            patch("core.views.testing_tools.notify_github_qa_signoff") as notify,
        ):
            response = qa_client.post(reverse("api_mark_ready"), data="{}", content_type="application/json")

        assert response.status_code == 500
        assert response.json()["success"] is False
        assert QAConfiguration.get_config().ready_for_prod is False
        # No sign-off happened, so nothing may arm the production workflow.
        notify.assert_not_called()

    @override_settings(IS_TESTING_ENV=True, SUPPORT_EMAIL=None)
    def test_missing_support_email_keeps_the_gate_closed(self, qa_client):
        from core.models import QAConfiguration

        response = qa_client.post(reverse("api_mark_ready"), data="{}", content_type="application/json")

        assert response.status_code == 500
        assert QAConfiguration.get_config().ready_for_prod is False

    def test_404_for_non_qa_user(self, authenticated_client):
        response = authenticated_client.post(reverse("api_mark_ready"), data="{}", content_type="application/json")
        assert response.status_code == 404


# ============================================================================
# /health/?deep=1 — ready_for_prod exposure (testing environment only)
# ============================================================================


class TestReadyForProdInHealth:
    """The flag rides the DEEP probe only: the shallow /health/ response must
    never touch the database, and the flag is a database row."""

    @override_settings(IS_TESTING_ENV=True)
    def test_deep_probe_reports_the_flag(self, client):
        from core.models import QAConfiguration

        response = client.get(reverse("health_check"), {"deep": "1"})
        assert response.status_code == 200
        assert response.json()["ready_for_prod"] is False

        config = QAConfiguration.get_config()
        config.ready_for_prod = True
        config.save()

        assert client.get(reverse("health_check"), {"deep": "1"}).json()["ready_for_prod"] is True

    @override_settings(IS_TESTING_ENV=True)
    def test_shallow_response_never_carries_it(self, client):
        response = client.get(reverse("health_check"))
        assert response.status_code == 200
        assert "ready_for_prod" not in response.json()

    @override_settings(IS_TESTING_ENV=False)
    def test_absent_outside_the_testing_environment(self, client):
        response = client.get(reverse("health_check"), {"deep": "1"})
        assert response.status_code == 200
        assert "ready_for_prod" not in response.json()


# ============================================================================
# manage.py set_ready_for_prod — the nightly deploy's reset hook
# ============================================================================


class TestSetReadyForProdCommand:
    def test_off_locks_and_on_unlocks(self):
        from django.core.management import call_command

        from core.models import QAConfiguration

        config = QAConfiguration.get_config()
        config.ready_for_prod = True
        config.save()

        call_command("set_ready_for_prod", "off")
        assert QAConfiguration.get_config().ready_for_prod is False

        call_command("set_ready_for_prod", "on")
        assert QAConfiguration.get_config().ready_for_prod is True

    def test_rejects_unknown_state(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command("set_ready_for_prod", "maybe")

    def test_on_arms_the_production_workflow_and_off_does_not(self):
        """A manual `on` is still a sign-off, so it fires the same
        repository_dispatch as the /testing/ button; `off` (the nightly
        deploy's reset) must never dispatch anything."""
        from django.core.management import call_command

        with patch("core.github_dispatch.notify_github_qa_signoff", return_value=True) as notify:
            call_command("set_ready_for_prod", "on")
        notify.assert_called_once_with()

        with patch("core.github_dispatch.notify_github_qa_signoff") as notify:
            call_command("set_ready_for_prod", "off")
        notify.assert_not_called()
