"""Unit tests for the parent portal session token (v1.9)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from students.models import ParentSessionToken

pytestmark = pytest.mark.django_db


class TestIssue:
    def test_creates_token_with_default_ttl(self, parent):
        token = ParentSessionToken.issue(parent)
        assert token.token and len(token.token) >= 16
        assert token.expires_at > timezone.now()
        assert token.used_at is None

    def test_custom_ttl(self, parent):
        token = ParentSessionToken.issue(parent, ttl_minutes=5)
        assert (token.expires_at - timezone.now()).total_seconds() < 6 * 60


class TestValidity:
    def test_fresh_token_is_valid(self, parent):
        token = ParentSessionToken.issue(parent)
        assert token.is_valid() is True

    def test_expired_token_invalid(self, parent):
        token = ParentSessionToken.issue(parent)
        token.expires_at = timezone.now() - timedelta(seconds=1)
        token.save(update_fields=["expires_at"])
        assert token.is_valid() is False

    def test_used_token_invalid(self, parent):
        token = ParentSessionToken.issue(parent)
        token.consume()
        assert token.is_valid() is False


class TestConsume:
    def test_consume_marks_used(self, parent):
        token = ParentSessionToken.issue(parent)
        assert token.consume() is True
        token.refresh_from_db()
        assert token.used_at is not None

    def test_consume_twice_returns_false(self, parent):
        token = ParentSessionToken.issue(parent)
        token.consume()
        assert token.consume() is False

    def test_consume_expired_returns_false(self, parent):
        token = ParentSessionToken.issue(parent)
        token.expires_at = timezone.now() - timedelta(minutes=1)
        token.save(update_fields=["expires_at"])
        assert token.consume() is False
