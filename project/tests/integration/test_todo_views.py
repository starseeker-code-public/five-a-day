"""Integration tests for core.views.todos — create/complete/history endpoints."""

import json
from datetime import date

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


class TestTodos:
    def test_create_missing_text(self, authenticated_client):
        response = authenticated_client.post(
            reverse("create_todo"),
            data=json.dumps({"text": "", "due_date": "2026-05-01"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_create_missing_date(self, authenticated_client):
        response = authenticated_client.post(
            reverse("create_todo"),
            data=json.dumps({"text": "Something", "due_date": ""}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_create_invalid_date(self, authenticated_client):
        response = authenticated_client.post(
            reverse("create_todo"),
            data=json.dumps({"text": "x", "due_date": "nope"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_create_success(self, authenticated_client):
        response = authenticated_client.post(
            reverse("create_todo"),
            data=json.dumps({"text": "Do it", "due_date": "2026-12-31"}),
            content_type="application/json",
        )
        assert response.status_code == 200

    def test_complete_todo(self, authenticated_client, db):
        from core.models import TodoItem

        todo = TodoItem.objects.create(text="Buy milk", due_date=date(2026, 5, 1))
        response = authenticated_client.post(reverse("complete_todo", kwargs={"todo_id": todo.id}))
        assert response.status_code == 200

    def test_history_list(self, authenticated_client):
        response = authenticated_client.get(reverse("history_list"))
        assert response.status_code == 200

    def test_history_list_with_offset(self, authenticated_client):
        response = authenticated_client.get(reverse("history_list") + "?offset=10")
        assert response.status_code == 200

    def test_history_list_invalid_offset(self, authenticated_client):
        response = authenticated_client.get(reverse("history_list") + "?offset=abc")
        assert response.status_code == 200
