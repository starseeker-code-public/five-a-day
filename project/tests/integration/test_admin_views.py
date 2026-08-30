"""Smoke tests for every registered Django admin view.

The `/admin/` site is the academy's escape hatch for everything the app UI
deliberately does not expose (`Student.waiting_priority`, `Group.max_students`,
promoting a Teacher to admin), so a 500 there is not cosmetic.

`*/admin.py` is excluded from coverage (`pyproject.toml [tool.coverage.run]`),
which is exactly how `EnrollmentAdmin.is_paid_display` shipped a `format_html()`
call with no interpolation arguments. That raises `TypeError: args or kwargs
must be provided.` on Django 6.0+ (it was a `RemovedInDjango60Warning` before),
but only on the branch taken once an enrollment is **fully paid** — so it stayed
invisible locally and only surfaced on the testing VM, where real payments
exist, taking down `/admin/billing/enrollment/` entirely.

These tests do not assert on rendered markup beyond the regression cases; the
contract is "no admin view 500s".
"""

import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.urls import reverse

from billing.models import Payment

pytestmark = pytest.mark.django_db


def _registered_models():
    """Every model registered on the default admin site, in a stable order."""
    return sorted(admin.site._registry, key=lambda m: (m._meta.app_label, m._meta.model_name))


def _model_id(model):
    return f"{model._meta.app_label}.{model._meta.model_name}"


ADMIN_MODELS = _registered_models()
ADMIN_MODEL_IDS = [_model_id(m) for m in ADMIN_MODELS]


@pytest.fixture
def admin_site_client(client):
    """A test client authenticated as a Django superuser.

    Needs both layers: `force_login` for `django.contrib.admin`'s own permission
    checks, and the `is_authenticated` session key for `SimpleAuthMiddleware`
    (which would otherwise redirect to /login/). A superuser bypasses
    `NON_ADMIN_ALLOWED_URL_NAMES`.
    """
    user = User.objects.create_superuser(
        username="admin-smoke",
        email="admin-smoke@example.com",
        password="not-used-by-force-login",
    )
    client.force_login(user)
    session = client.session
    session["is_authenticated"] = True
    session["username"] = user.username
    session.save()
    return client


class TestAdminSiteSmoke:
    def test_index_renders(self, admin_site_client):
        response = admin_site_client.get(reverse("admin:index"))
        assert response.status_code == 200

    @pytest.mark.parametrize("model", ADMIN_MODELS, ids=ADMIN_MODEL_IDS)
    def test_changelist_renders(self, admin_site_client, model):
        url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
        assert admin_site_client.get(url).status_code == 200

    @pytest.mark.parametrize("model", ADMIN_MODELS, ids=ADMIN_MODEL_IDS)
    def test_changelist_search_and_ordering_render(self, admin_site_client, model):
        """`?q=` and `?o=` take different SQL paths than the bare changelist."""
        url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
        for query in ("?q=a", "?o=1", "?o=-1"):
            response = admin_site_client.get(url + query)
            assert response.status_code == 200, f"{url}{query} returned {response.status_code}"

    @pytest.mark.parametrize("model", ADMIN_MODELS, ids=ADMIN_MODEL_IDS)
    def test_add_form_renders_or_is_forbidden(self, admin_site_client, model):
        """403 is legitimate — SiteConfiguration is a singleton, AuditLog is read-only."""
        url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_add")
        assert admin_site_client.get(url).status_code in (200, 403)


class TestEnrollmentAdminChangelist:
    """Regression: both branches of `EnrollmentAdmin.is_paid_display`.

    The paid branch is the one that used to raise; keep both covered so a future
    edit cannot drop the interpolation argument from either.
    """

    def _changelist(self, client):
        return client.get(reverse("admin:billing_enrollment_changelist"))

    def test_renders_unpaid_enrollment(self, admin_site_client, active_enrollment):
        response = self._changelist(admin_site_client)
        assert response.status_code == 200
        assert "Pending" in response.content.decode()

    def test_renders_fully_paid_enrollment(self, admin_site_client, active_enrollment, completed_payment):
        """`completed_payment` is 54.00 against a 54.00 enrollment, so is_paid is True."""
        assert active_enrollment.is_paid is True
        response = self._changelist(admin_site_client)
        assert response.status_code == 200
        assert "Paid" in response.content.decode()

    def test_renders_overpaid_enrollment(self, admin_site_client, active_enrollment, completed_payment):
        """Overpayment stays on the paid branch and must not produce a negative remainder."""
        completed_payment.amount = active_enrollment.final_amount * 2
        completed_payment.save()
        assert active_enrollment.remaining_amount == 0
        assert self._changelist(admin_site_client).status_code == 200

    def test_renders_enrollment_with_cancelled_payments_only(self, admin_site_client, active_enrollment, student):
        """Cancelled money must not count as paid — the unpaid branch still renders."""
        Payment.objects.create(
            student=student,
            enrollment=active_enrollment,
            payment_type="monthly",
            payment_method="cash",
            amount=active_enrollment.final_amount,
            payment_status="cancelled",
            due_date=active_enrollment.enrollment_date,
            concept="Cancelled duplicate",
        )
        assert active_enrollment.is_paid is False
        response = self._changelist(admin_site_client)
        assert response.status_code == 200
        assert "Pending" in response.content.decode()
