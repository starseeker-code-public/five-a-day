"""Integration tests for the PWA endpoints (v1.12)."""

import json

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


class TestWebManifest:
    def test_returns_manifest(self, client):
        response = client.get(reverse("web_manifest"))
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["name"] == "Five a Day"
        assert data["display"] == "standalone"
        assert data["scope"] == "/"
        assert len(data["icons"]) >= 1
        assert data["theme_color"].startswith("#")

    def test_accessible_without_auth(self, client):
        # The unauthenticated client should still be able to fetch the manifest.
        response = client.get(reverse("web_manifest"))
        assert response.status_code == 200

    def test_cache_control_set(self, client):
        response = client.get(reverse("web_manifest"))
        assert "max-age" in response["Cache-Control"]


class TestServiceWorker:
    def test_returns_js(self, client):
        response = client.get(reverse("service_worker"))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/javascript"

    def test_contains_expected_directives(self, client):
        response = client.get(reverse("service_worker"))
        body = response.content.decode()
        assert "CACHE_NAME" in body
        assert 'self.addEventListener("install"' in body
        assert 'self.addEventListener("fetch"' in body

    def test_service_worker_allowed_header(self, client):
        response = client.get(reverse("service_worker"))
        assert response["Service-Worker-Allowed"] == "/"

    def test_version_in_cache_key(self, client, settings):
        settings.APP_VERSION = "9.9.9"
        response = client.get(reverse("service_worker"))
        assert b"fiveaday-v9.9.9" in response.content

    def test_accessible_without_auth(self, client):
        response = client.get(reverse("service_worker"))
        assert response.status_code == 200
