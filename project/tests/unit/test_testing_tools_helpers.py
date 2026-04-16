"""Unit tests for core.views.testing_tools._git_info helper.

Direct call with subprocess mocked. The @qa_access_required view endpoints
that call this helper live in integration/test_testing_tools.py.
"""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.django_db


class TestGitInfo:
    def test_git_info_success_path(self):
        """Non-zero returncode branch (lines 35-45)."""
        from core.views.testing_tools import _git_info

        with patch("core.views.testing_tools.subprocess.run") as mock_run:
            # First call returns a valid commit format
            mock_log = MagicMock(returncode=0, stdout="sha1\nshort1\nmsg\nauthor\n2026-04-15")
            mock_branch = MagicMock(returncode=0, stdout="development\n")
            mock_run.side_effect = [mock_log, mock_branch]

            info = _git_info()
        assert info["branch"] == "development"
        assert info["commit_id_full"] == "sha1"

    def test_git_info_log_returncode_nonzero(self):
        from core.views.testing_tools import _git_info

        with patch("core.views.testing_tools.subprocess.run") as mock_run:
            mock_log = MagicMock(returncode=1, stdout="")
            mock_run.return_value = mock_log
            info = _git_info()
        assert info == {}
