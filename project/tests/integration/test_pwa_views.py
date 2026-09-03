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

    def test_static_is_cache_first_when_not_debug(self, client, settings):
        """Production serves content-hashed filenames, so cache-first is
        optimal there and must stay."""
        settings.DEBUG = False
        body = client.get(reverse("service_worker")).content.decode()
        assert "const DEV = false;" in body

    def test_static_bypasses_the_sw_in_development(self, client, settings):
        """Dev serves the BARE path (/static/js/management.js) and CACHE_NAME is
        keyed on APP_VERSION, so between releases an edited asset was served
        from cache forever. The symptom is nasty: the HTML is `no-cache` so a
        template change lands instantly while its script stays stale — a new
        button renders and clicking it does nothing."""
        settings.DEBUG = True
        body = client.get(reverse("service_worker")).content.decode()
        assert "const DEV = true;" in body
        # The bypass has to be wired into isCacheable, not just declared.
        assert 'if (path.startsWith("/static/") || path.startsWith("/media/")) return !DEV;' in body
