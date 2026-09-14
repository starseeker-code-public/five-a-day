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


class TestSupportCategoryIsValidatedNotTrusted:
    """The category reaches an email SUBJECT and its label reaches the body.

    Both used to come straight off the JSON payload — `category.upper()` was
    interpolated into `f"[{category.upper()}] Ticket de Soporte"` and
    `category_display` was printed verbatim. Django refuses newlines in headers,
    so it was never header injection; what it was is an unvalidated free-text
    channel into a mail the academy's support address receives and reads.
    Both now come from `SUPPORT_CATEGORIES`, keyed on the four values
    `support.js` can actually send.
    """

    def _send(self, client, payload):
        with patch("django.core.mail.send_mail") as mock_mail:
            response = client.post(
                reverse("submit_support_ticket"),
                data=json.dumps({"message": "a long enough message", **payload}),
                content_type="application/json",
            )
        assert response.status_code == 200
        return mock_mail.call_args.kwargs

    @override_settings(SUPPORT_EMAIL="sup@test.com")
    def test_a_known_category_keeps_its_own_label(self, authenticated_client):
        sent = self._send(authenticated_client, {"category": "database"})

        assert sent["subject"] == "[DATABASE] Ticket de Soporte - Five a Day"
        assert "Datos / Base de datos" in sent["message"]

    @override_settings(SUPPORT_EMAIL="sup@test.com")
    def test_an_unknown_category_falls_back_instead_of_reaching_the_subject(self, authenticated_client):
        sent = self._send(authenticated_client, {"category": "URGENTE-CLICK-AQUI"})

        assert sent["subject"] == "[EXCEPTION] Ticket de Soporte - Five a Day"
        assert "URGENTE" not in sent["subject"]
        assert "Otro" in sent["message"]

    @override_settings(SUPPORT_EMAIL="sup@test.com")
    def test_a_client_supplied_label_is_ignored(self, authenticated_client):
        """`category_display` used to be whatever the client sent. The label now
        comes from the category, so the two cannot disagree either."""
        sent = self._send(
            authenticated_client,
            {"category": "frontend", "category_display": "Cuenta verificada por el administrador"},
        )

        assert "Interfaz / Problemas visuales" in sent["message"]
        assert "Cuenta verificada" not in sent["message"]
