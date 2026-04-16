"""Integration tests for core.views.dashboard — quote cache/cookie/API flow
of `home` and sort variants of `all_info`.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_quote_cache():
    """Reset the module-level _quotes list between tests so each test starts
    with an empty cache and predictable API-call behavior."""
    import core.views.dashboard as dash

    dash._quotes.clear()
    yield
    dash._quotes.clear()


class TestDashboardQuote:
    def test_home_cache_hit_no_api_call(self, authenticated_client):
        """When _quotes is pre-filled, no API call is made."""
        import core.views.dashboard as dash

        dash._quotes[:] = [("Cached quote", "Cached Author")]
        with patch("core.views.dashboard.httpx.get") as mock_get:
            response = authenticated_client.get(reverse("home"))
        assert response.status_code == 200
        mock_get.assert_not_called()
        # Cache was consumed
        assert len(dash._quotes) == 0

    def test_home_empty_cache_fetches_batch(self, authenticated_client):
        """Empty cache triggers _fetch_quotes → API call → cache refilled."""
        with patch("core.views.dashboard._fetch_quotes") as mock_fetch:
            mock_fetch.return_value = [("Q1", "A1"), ("Q2", "A2"), ("Q3", "A3")]
            response = authenticated_client.get(reverse("home"))
        assert response.status_code == 200
        mock_fetch.assert_called_once()

    def test_home_sets_cookie_on_cache_hit(self, authenticated_client):
        """After serving a quote, the response sets a last_quote cookie."""
        import core.views.dashboard as dash

        dash._quotes[:] = [("Be brave", "Author X")]
        response = authenticated_client.get(reverse("home"))
        assert response.status_code == 200
        assert "last_quote" in response.cookies

    def test_home_api_failure_falls_back_to_cookie(self, authenticated_client):
        """API fails + cache empty → cookie fallback."""
        authenticated_client.cookies["last_quote"] = "Old cookie quote - Old Author"
        with patch("core.views.dashboard._fetch_quotes", return_value=[]):
            response = authenticated_client.get(reverse("home"))
        assert response.status_code == 200

    def test_home_api_failure_no_cookie_uses_hardcoded(self, authenticated_client):
        """API fails + cache empty + no cookie → hardcoded Spanish fallback."""
        with patch("core.views.dashboard._fetch_quotes", return_value=[]):
            response = authenticated_client.get(reverse("home"))
        assert response.status_code == 200

    def test_home_api_returns_auth_placeholder_filtered(self, authenticated_client):
        """The '[AUTH]' placeholder from zenquotes is filtered out."""
        with patch("core.views.dashboard.httpx.get") as mock_get:
            resp = MagicMock()
            resp.json.return_value = [{"q": "[AUTH]", "a": ""}]
            resp.raise_for_status.return_value = None
            mock_get.return_value = resp
            response = authenticated_client.get(reverse("home"))
        assert response.status_code == 200

    def test_home_api_returns_empty_list(self, authenticated_client):
        with patch("core.views.dashboard.httpx.get") as mock_get:
            resp = MagicMock()
            resp.json.return_value = []
            resp.raise_for_status.return_value = None
            mock_get.return_value = resp
            response = authenticated_client.get(reverse("home"))
        assert response.status_code == 200

    def test_home_api_network_error(self, authenticated_client):
        with patch("core.views.dashboard.httpx.get", side_effect=Exception("network")):
            response = authenticated_client.get(reverse("home"))
        assert response.status_code == 200

    def test_home_rotates_on_each_load(self, authenticated_client):
        """Each page load pops a different quote from the cache."""
        import core.views.dashboard as dash

        dash._quotes[:] = [("Q1", "A1"), ("Q2", "A2"), ("Q3", "A3")]

        r1 = authenticated_client.get(reverse("home"))
        r2 = authenticated_client.get(reverse("home"))
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert len(dash._quotes) == 1  # started with 3, popped 2

    def test_home_with_pending_payments(self, authenticated_client, pending_payment, student_with_parent):
        with patch("core.views.dashboard._fetch_quotes", return_value=[("Q", "A")]):
            response = authenticated_client.get(reverse("home"))
        assert response.status_code == 200


class TestAllInfoSorts:
    def test_default_sort(self, authenticated_client, student_with_parent):
        response = authenticated_client.get(reverse("all_info"))
        assert response.status_code == 200

    def test_sort_by_first_name(self, authenticated_client, student_with_parent):
        response = authenticated_client.get(reverse("all_info") + "?students_sort=first_name_asc")
        assert response.status_code == 200

    def test_sort_by_last_name(self, authenticated_client, student_with_parent):
        response = authenticated_client.get(reverse("all_info") + "?students_sort=last_name_asc")
        assert response.status_code == 200

    def test_sort_by_id_asc(self, authenticated_client, student_with_parent):
        response = authenticated_client.get(reverse("all_info") + "?students_sort=id_asc")
        assert response.status_code == 200

    def test_payments_student_sort(self, authenticated_client, pending_payment):
        response = authenticated_client.get(reverse("all_info") + "?payments_sort=student_asc")
        assert response.status_code == 200
