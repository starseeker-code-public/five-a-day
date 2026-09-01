"""Tests for core.views.features — the Desarrollos (QA epic) board.

Same access gate as the testing dashboard: @qa_access_required requires
settings.IS_TESTING_ENV and a logged-in ADMIN Teacher, so every test uses
override_settings + the qa_client fixture from test_testing_tools.
"""

import json
from datetime import date, timedelta

import pytest
from django.test import override_settings
from django.urls import reverse

from core.models import BacklogTask, Feature

# The QA-authenticated client fixture lives with the testing-tools tests; it is
# imported so pytest resolves `qa_client` here too.
from tests.integration.test_testing_tools import qa_client  # noqa: F401

pytestmark = pytest.mark.django_db

QA_SETTINGS = override_settings(IS_TESTING_ENV=True)


@pytest.fixture
def feature(db):
    return Feature.objects.create(title="Portal de padres v2", description="h2. Resumen", created_by="QA")


# ============================================================================
# Model
# ============================================================================


class TestFeatureModel:
    def test_deadline_is_null_by_default(self, feature):
        """A development is recorded long before anyone commits to a date."""
        assert feature.deadline is None
        assert feature.days_left is None
        assert feature.is_overdue is False

    def test_is_overdue_when_deadline_passed_and_not_done(self, feature):
        feature.deadline = date.today() - timedelta(days=1)
        feature.save()
        assert feature.is_overdue is True
        assert feature.days_left == -1

    def test_done_feature_is_never_overdue(self, feature):
        feature.deadline = date.today() - timedelta(days=30)
        feature.status = "done"
        feature.save()
        assert feature.is_overdue is False

    def test_future_deadline_is_not_overdue(self, feature):
        feature.deadline = date.today() + timedelta(days=5)
        feature.save()
        assert feature.is_overdue is False
        assert feature.days_left == 5

    def test_progress_counters(self, feature):
        assert feature.progress_percent == 0
        BacklogTask.objects.create(title="A", feature=feature)
        BacklogTask.objects.create(title="B", feature=feature, status="done")
        assert feature.task_count == 2
        assert feature.done_task_count == 1
        assert feature.progress_percent == 50

    def test_deleting_a_feature_keeps_its_tasks(self, feature):
        """SET_NULL — losing the epic must never take the work items with it."""
        task = BacklogTask.objects.create(title="Sigue viva", feature=feature)
        feature.delete()
        task.refresh_from_db()
        assert task.feature is None

    def test_status_labels_are_spanish(self, feature):
        assert feature.get_status_display() == "Abierto"

    def test_str(self, feature):
        assert str(feature) == "Portal de padres v2"


# ============================================================================
# features_view / feature_detail_view (GET)
# ============================================================================


class TestFeatureViews:
    @QA_SETTINGS
    def test_board_renders(self, qa_client, feature):  # noqa: F811
        response = qa_client.get(reverse("features"))
        assert response.status_code == 200
        assert "description_template" in response.context
        assert list(response.context["features"]) == [feature]

    @QA_SETTINGS
    def test_board_puts_done_features_last(self, qa_client):  # noqa: F811
        done = Feature.objects.create(title="Ya hecho", status="done")
        live = Feature.objects.create(title="En curso")
        response = qa_client.get(reverse("features"))
        assert list(response.context["features"]) == [live, done]

    @QA_SETTINGS
    def test_board_carries_the_jira_template(self, qa_client):  # noqa: F811
        response = qa_client.get(reverse("features"))
        template = response.context["description_template"]
        for heading in ("h2. Resumen", "h2. Alcance", "h2. Criterios de aceptacion"):
            assert heading in template

    def test_board_404_for_non_qa_user(self, authenticated_client):
        assert authenticated_client.get(reverse("features")).status_code == 404

    @QA_SETTINGS
    def test_detail_renders_with_its_tasks(self, qa_client, feature):  # noqa: F811
        BacklogTask.objects.create(title="Tarea 1", feature=feature)
        response = qa_client.get(reverse("feature_detail", args=[feature.id]))
        assert response.status_code == 200
        assert response.context["feature"] == feature
        assert [t.title for t in response.context["tasks"]] == ["Tarea 1"]

    @QA_SETTINGS
    def test_detail_404_for_unknown_feature(self, qa_client):  # noqa: F811
        assert qa_client.get(reverse("feature_detail", args=[9999])).status_code == 404

    def test_detail_404_for_non_qa_user(self, authenticated_client, feature):
        assert authenticated_client.get(reverse("feature_detail", args=[feature.id])).status_code == 404


# ============================================================================
# api_create_feature (POST)
# ============================================================================


class TestApiCreateFeature:
    @QA_SETTINGS
    def test_create_success(self, qa_client):  # noqa: F811
        response = qa_client.post(
            reverse("api_create_feature"),
            data=json.dumps({"title": "Nuevo informe", "description": "h2. Resumen"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["feature"]["title"] == "Nuevo informe"
        # No deadline unless one was given — the field's default.
        assert data["feature"]["deadline"] is None
        assert Feature.objects.count() == 1

    @QA_SETTINGS
    def test_create_with_deadline(self, qa_client):  # noqa: F811
        response = qa_client.post(
            reverse("api_create_feature"),
            data=json.dumps({"title": "Con fecha", "deadline": "2026-12-31"}),
            content_type="application/json",
        )
        assert response.json()["feature"]["deadline"] == "2026-12-31"
        assert Feature.objects.get().deadline == date(2026, 12, 31)

    @QA_SETTINGS
    def test_blank_deadline_is_accepted_as_no_deadline(self, qa_client):  # noqa: F811
        response = qa_client.post(
            reverse("api_create_feature"),
            data=json.dumps({"title": "Sin fecha", "deadline": ""}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert Feature.objects.get().deadline is None

    @QA_SETTINGS
    def test_invalid_deadline_returns_400(self, qa_client):  # noqa: F811
        response = qa_client.post(
            reverse("api_create_feature"),
            data=json.dumps({"title": "Mala fecha", "deadline": "31/12/2026"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert Feature.objects.count() == 0

    @QA_SETTINGS
    def test_missing_title_returns_400(self, qa_client):  # noqa: F811
        response = qa_client.post(
            reverse("api_create_feature"),
            data=json.dumps({"title": "   "}),
            content_type="application/json",
        )
        assert response.status_code == 400

    @QA_SETTINGS
    def test_invalid_json_returns_400(self, qa_client):  # noqa: F811
        response = qa_client.post(reverse("api_create_feature"), data="not-json", content_type="application/json")
        assert response.status_code == 400

    @override_settings(IS_TESTING_ENV=True, SUPPORT_EMAIL="sup@test.com")
    def test_creation_emails_support(self, qa_client):  # noqa: F811
        from django.core import mail

        qa_client.post(
            reverse("api_create_feature"),
            data=json.dumps({"title": "Avisadme"}),
            content_type="application/json",
        )
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["sup@test.com"]
        assert "[DESARROLLO]" in mail.outbox[0].subject

    def test_404_for_non_qa_user(self, authenticated_client):
        response = authenticated_client.post(
            reverse("api_create_feature"),
            data=json.dumps({"title": "x"}),
            content_type="application/json",
        )
        assert response.status_code == 404


# ============================================================================
# api_update_feature (POST)
# ============================================================================


class TestApiUpdateFeature:
    @QA_SETTINGS
    def test_update_status(self, qa_client, feature):  # noqa: F811
        response = qa_client.post(
            reverse("api_update_feature", args=[feature.id]),
            data=json.dumps({"status": "in_progress"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        feature.refresh_from_db()
        assert feature.status == "in_progress"

    @QA_SETTINGS
    def test_update_deadline(self, qa_client, feature):  # noqa: F811
        qa_client.post(
            reverse("api_update_feature", args=[feature.id]),
            data=json.dumps({"deadline": "2027-01-15"}),
            content_type="application/json",
        )
        feature.refresh_from_db()
        assert feature.deadline == date(2027, 1, 15)

    @QA_SETTINGS
    def test_null_deadline_clears_it(self, qa_client, feature):  # noqa: F811
        feature.deadline = date(2027, 1, 15)
        feature.save()
        qa_client.post(
            reverse("api_update_feature", args=[feature.id]),
            data=json.dumps({"deadline": None}),
            content_type="application/json",
        )
        feature.refresh_from_db()
        assert feature.deadline is None

    @QA_SETTINGS
    def test_status_only_payload_does_not_touch_the_deadline(self, qa_client, feature):  # noqa: F811
        """Board and detail page post different subsets — neither may clobber
        the other's field."""
        feature.deadline = date(2027, 3, 1)
        feature.save()
        qa_client.post(
            reverse("api_update_feature", args=[feature.id]),
            data=json.dumps({"status": "done"}),
            content_type="application/json",
        )
        feature.refresh_from_db()
        assert feature.deadline == date(2027, 3, 1)

    @QA_SETTINGS
    def test_update_title_and_description(self, qa_client, feature):  # noqa: F811
        qa_client.post(
            reverse("api_update_feature", args=[feature.id]),
            data=json.dumps({"title": "Otro titulo", "description": "h2. Nuevo"}),
            content_type="application/json",
        )
        feature.refresh_from_db()
        assert feature.title == "Otro titulo"
        assert feature.description == "h2. Nuevo"

    @QA_SETTINGS
    def test_blank_title_returns_400(self, qa_client, feature):  # noqa: F811
        response = qa_client.post(
            reverse("api_update_feature", args=[feature.id]),
            data=json.dumps({"title": "  "}),
            content_type="application/json",
        )
        assert response.status_code == 400

    @QA_SETTINGS
    def test_invalid_status_returns_400(self, qa_client, feature):  # noqa: F811
        response = qa_client.post(
            reverse("api_update_feature", args=[feature.id]),
            data=json.dumps({"status": "wat"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        feature.refresh_from_db()
        assert feature.status == "open"

    @QA_SETTINGS
    def test_invalid_deadline_returns_400(self, qa_client, feature):  # noqa: F811
        response = qa_client.post(
            reverse("api_update_feature", args=[feature.id]),
            data=json.dumps({"deadline": "manana"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    @QA_SETTINGS
    def test_empty_payload_returns_400(self, qa_client, feature):  # noqa: F811
        response = qa_client.post(
            reverse("api_update_feature", args=[feature.id]),
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 400

    @QA_SETTINGS
    def test_unknown_feature_returns_404(self, qa_client):  # noqa: F811
        response = qa_client.post(
            reverse("api_update_feature", args=[9999]),
            data=json.dumps({"status": "done"}),
            content_type="application/json",
        )
        assert response.status_code == 404

    @QA_SETTINGS
    def test_marking_done_emails_admin_teachers(self, qa_client, feature):  # noqa: F811
        from django.core import mail

        mail.outbox.clear()
        qa_client.post(
            reverse("api_update_feature", args=[feature.id]),
            data=json.dumps({"status": "done"}),
            content_type="application/json",
        )
        assert len(mail.outbox) == 1
        assert "[DESARROLLO][HECHO]" in mail.outbox[0].subject

    @QA_SETTINGS
    def test_re_marking_done_does_not_email_again(self, qa_client, feature):  # noqa: F811
        from django.core import mail

        feature.status = "done"
        feature.save()
        mail.outbox.clear()
        qa_client.post(
            reverse("api_update_feature", args=[feature.id]),
            data=json.dumps({"status": "done"}),
            content_type="application/json",
        )
        assert mail.outbox == []


# ============================================================================
# api_create_feature_task (POST) — tasks move to the backlog
# ============================================================================


class TestApiCreateFeatureTask:
    @QA_SETTINGS
    def test_task_lands_in_the_backlog_linked_to_the_feature(self, qa_client, feature):  # noqa: F811
        response = qa_client.post(
            reverse("api_create_feature_task", args=[feature.id]),
            data=json.dumps({"title": "Modelo Feature", "description": "Con FK", "priority": "high"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["task"]["priority"] == "high"
        assert data["task_count"] == 1

        task = BacklogTask.objects.get()
        assert task.feature == feature
        assert task.status == "open"

    @QA_SETTINGS
    def test_priority_defaults_to_medium(self, qa_client, feature):  # noqa: F811
        qa_client.post(
            reverse("api_create_feature_task", args=[feature.id]),
            data=json.dumps({"title": "Sin prioridad explicita"}),
            content_type="application/json",
        )
        assert BacklogTask.objects.get().priority == "medium"

    @QA_SETTINGS
    def test_invalid_priority_returns_400(self, qa_client, feature):  # noqa: F811
        response = qa_client.post(
            reverse("api_create_feature_task", args=[feature.id]),
            data=json.dumps({"title": "Ok", "priority": "urgent"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert BacklogTask.objects.count() == 0

    @QA_SETTINGS
    def test_missing_title_returns_400(self, qa_client, feature):  # noqa: F811
        response = qa_client.post(
            reverse("api_create_feature_task", args=[feature.id]),
            data=json.dumps({"title": ""}),
            content_type="application/json",
        )
        assert response.status_code == 400

    @QA_SETTINGS
    def test_unknown_feature_returns_404(self, qa_client):  # noqa: F811
        response = qa_client.post(
            reverse("api_create_feature_task", args=[9999]),
            data=json.dumps({"title": "x"}),
            content_type="application/json",
        )
        assert response.status_code == 404

    @override_settings(IS_TESTING_ENV=True, SUPPORT_EMAIL="sup@test.com")
    def test_task_creation_emails_support_naming_the_feature(self, qa_client, feature):  # noqa: F811
        from django.core import mail

        mail.outbox.clear()
        qa_client.post(
            reverse("api_create_feature_task", args=[feature.id]),
            data=json.dumps({"title": "Tarea con aviso"}),
            content_type="application/json",
        )
        assert len(mail.outbox) == 1
        assert "[BACKLOG]" in mail.outbox[0].subject
        assert feature.title in mail.outbox[0].body

    @QA_SETTINGS
    def test_feature_task_shows_up_on_the_backlog_dashboard(self, qa_client, feature):  # noqa: F811
        qa_client.post(
            reverse("api_create_feature_task", args=[feature.id]),
            data=json.dumps({"title": "Aparece en /testing/"}),
            content_type="application/json",
        )
        response = qa_client.get(reverse("testing_tools"))
        assert [t.title for t in response.context["tasks"]] == ["Aparece en /testing/"]


# ============================================================================
# export_features (GET)
# ============================================================================


class TestExportFeatures:
    @QA_SETTINGS
    def test_json_export_defaults_to_active_scope(self, qa_client, feature):  # noqa: F811
        Feature.objects.create(title="Hecho", status="done")
        response = qa_client.get(reverse("export_features"))
        payload = json.loads(response.content)
        assert response["Content-Type"].startswith("application/json")
        assert payload["count"] == 1
        assert payload["features"][0]["title"] == feature.title

    @QA_SETTINGS
    def test_json_export_all_scope_includes_done(self, qa_client, feature):  # noqa: F811
        Feature.objects.create(title="Hecho", status="done")
        response = qa_client.get(reverse("export_features") + "?scope=all")
        assert json.loads(response.content)["count"] == 2

    @QA_SETTINGS
    def test_csv_export(self, qa_client, feature):  # noqa: F811
        BacklogTask.objects.create(title="T", feature=feature, status="done")
        response = qa_client.get(reverse("export_features") + "?format=csv")
        assert response["Content-Type"].startswith("text/csv")
        assert "attachment" in response["Content-Disposition"]
        body = response.content.decode("utf-8")
        assert feature.title in body

    @QA_SETTINGS
    def test_csv_export_with_no_rows_still_writes_a_header(self, qa_client):  # noqa: F811
        response = qa_client.get(reverse("export_features") + "?format=csv")
        assert response.status_code == 200
        assert "title" in response.content.decode("utf-8")

    def test_404_for_non_qa_user(self, authenticated_client):
        assert authenticated_client.get(reverse("export_features")).status_code == 404
