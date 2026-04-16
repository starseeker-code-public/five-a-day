"""Integration tests for core.views.support.submit_support_ticket."""

import json
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import reverse

pytestmark = pytest.mark.django_db


class TestSubmitSupportTicket:
    @override_settings(SUPPORT_EMAIL="sup@test.com")
    def test_success(self, authenticated_client):
        with patch("django.core.mail.send_mail") as mock_mail:
            response = authenticated_client.post(
                reverse("submit_support_ticket"),
                data=json.dumps(
                    {
                        "category": "bug",
                        "category_display": "Bug",
                        "message": "Something is broken here",
                        "current_url": "/x",
                    }
                ),
                content_type="application/json",
            )
        assert response.status_code == 200
        mock_mail.assert_called_once()

    def test_short_message_rejected(self, authenticated_client):
        response = authenticated_client.post(
            reverse("submit_support_ticket"),
            data=json.dumps({"message": "short"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    @override_settings(SUPPORT_EMAIL=None)
    def test_no_support_email_configured(self, authenticated_client):
        response = authenticated_client.post(
            reverse("submit_support_ticket"),
            data=json.dumps({"message": "long enough message here"}),
            content_type="application/json",
        )
        assert response.status_code == 500

    def test_bad_json(self, authenticated_client):
        response = authenticated_client.post(
            reverse("submit_support_ticket"),
            data="not-json",
            content_type="application/json",
        )
        assert response.status_code == 400

    @override_settings(SUPPORT_EMAIL="sup@test.com")
    def test_unexpected_exception(self, authenticated_client):
        with patch("django.core.mail.send_mail", side_effect=RuntimeError("smtp")):
            response = authenticated_client.post(
                reverse("submit_support_ticket"),
                data=json.dumps({"message": "long message here please"}),
                content_type="application/json",
            )
        assert response.status_code == 500
