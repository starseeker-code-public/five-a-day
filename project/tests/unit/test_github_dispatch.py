"""
notify_github_qa_signoff() — the repository_dispatch that arms `Deploy production`.

The contract under test is FAIL-SOFT: the QA sign-off flag in the database is
the source of truth and the nightly workflow_run re-trigger is the fallback
arming path, so this helper must return False (never raise) on every path that
does not end with GitHub accepting the event — wrong environment, missing
token, HTTP error, network failure. It must also never make a request it is
not configured to make.
"""

from unittest.mock import MagicMock, patch

import httpx
from django.test import override_settings

from core.github_dispatch import DISPATCH_EVENT_TYPE, notify_github_qa_signoff


class TestNotifyGithubQaSignoff:
    @override_settings(IS_TESTING_ENV=False)
    def test_inert_outside_the_testing_environment(self, monkeypatch):
        """Only the QA VM may dispatch — dev and production stay silent even
        with a token configured."""
        monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "ghp_token")
        with patch("core.github_dispatch.httpx.post") as post:
            assert notify_github_qa_signoff() is False
        post.assert_not_called()

    @override_settings(IS_TESTING_ENV=True)
    def test_missing_token_is_a_quiet_no_op(self, monkeypatch):
        monkeypatch.delenv("GITHUB_DISPATCH_TOKEN", raising=False)
        with patch("core.github_dispatch.httpx.post") as post:
            assert notify_github_qa_signoff() is False
        post.assert_not_called()

    @override_settings(IS_TESTING_ENV=True)
    def test_success_sends_the_event_github_expects(self, monkeypatch):
        monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "ghp_token")
        monkeypatch.setenv("GITHUB_DISPATCH_REPO", "owner/repo")
        response = MagicMock()
        response.raise_for_status.return_value = None

        with patch("core.github_dispatch.httpx.post", return_value=response) as post:
            assert notify_github_qa_signoff() is True

        post.assert_called_once()
        args, kwargs = post.call_args
        assert args[0] == "https://api.github.com/repos/owner/repo/dispatches"
        # The event type is the contract with deploy-production.yml's
        # `repository_dispatch: types:` filter — renaming either side alone
        # silently disarms the same-day trigger.
        assert kwargs["json"] == {"event_type": DISPATCH_EVENT_TYPE}
        assert kwargs["headers"]["Authorization"] == "Bearer ghp_token"

    @override_settings(IS_TESTING_ENV=True)
    def test_api_rejection_fails_soft(self, monkeypatch):
        monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "ghp_token")
        response = MagicMock()
        response.raise_for_status.side_effect = httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock())
        with patch("core.github_dispatch.httpx.post", return_value=response):
            assert notify_github_qa_signoff() is False

    @override_settings(IS_TESTING_ENV=True)
    def test_network_failure_fails_soft(self, monkeypatch):
        monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "ghp_token")
        with patch("core.github_dispatch.httpx.post", side_effect=httpx.ConnectError("boom")):
            assert notify_github_qa_signoff() is False
