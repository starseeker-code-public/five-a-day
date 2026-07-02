"""
Parent portal — magic-link session token (v1.9).

Kept in its own module rather than folded into `students.models` so the
top-level student data model stays focused. Imported from `students.models`
so Django's app-registry picks it up.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.db import models
from django.utils import timezone


def _fresh_token() -> str:
    """32 hex chars — 128 bits of entropy, matches Django's default token widths."""
    return secrets.token_hex(16)


class ParentSessionToken(models.Model):
    """
    Single-use magic-link token. Emailed to the parent; consumed on the
    /parent/login/<token>/ endpoint, which sets the session and marks the
    token as used. Expires 30 minutes after creation.
    """

    parent = models.ForeignKey("students.Parent", on_delete=models.CASCADE, related_name="session_tokens")
    token = models.CharField(max_length=64, unique=True, default=_fresh_token)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "parent_session_tokens"
        indexes = [
            models.Index(fields=["token"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return f"{self.parent} (expires {self.expires_at:%Y-%m-%d %H:%M})"

    @classmethod
    def issue(cls, parent, ttl_minutes: int = 30) -> ParentSessionToken:
        return cls.objects.create(
            parent=parent,
            expires_at=timezone.now() + timedelta(minutes=ttl_minutes),
        )

    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()

    def consume(self) -> bool:
        if not self.is_valid():
            return False
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])
        return True
