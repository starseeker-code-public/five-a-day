"""Unit tests for core.views.errors handler functions.

Handlers are plain view functions — called directly with a RequestFactory
request. No HTTP stack needed.
"""

import pytest
from django.test import RequestFactory

pytestmark = pytest.mark.django_db


class TestErrorHandlers:
    def test_handler400(self, authenticated_client):
        from core.views.errors import handler400

        rf = RequestFactory()
        req = rf.get("/")
        response = handler400(req)
        assert response.status_code == 400

    def test_handler403(self, authenticated_client):
        from core.views.errors import handler403

        rf = RequestFactory()
        req = rf.get("/")
        response = handler403(req)
        assert response.status_code == 403

    def test_handler404(self, authenticated_client):
        from core.views.errors import handler404

        rf = RequestFactory()
        req = rf.get("/")
        response = handler404(req)
        assert response.status_code == 404

    def test_handler405(self, authenticated_client):
        from core.views.errors import handler405

        rf = RequestFactory()
        req = rf.get("/")
        response = handler405(req)
        assert response.status_code == 405

    def test_handler500(self, authenticated_client):
        from core.views.errors import handler500

        rf = RequestFactory()
        req = rf.get("/")
        response = handler500(req)
        assert response.status_code == 500
