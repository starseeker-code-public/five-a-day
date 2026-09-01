"""The app version must agree everywhere it appears.

`pyproject.toml` is the single source of truth. `make version x.y.z` (and the
`make pc-run` auto patch-bump) update it, the README badge and `uv.lock`;
`settings.APP_VERSION` derives from it at import time rather than carrying a copy.

These tests exist because the copies used to drift silently: v1.20.0 shipped with
the settings bump missing entirely and needed a follow-up commit to repair it.
Nothing about a stale version looks wrong until you read /health/ in production.
"""

import re
import tomllib
from pathlib import Path

import pytest
from django.conf import settings

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def pyproject_version():
    with open(REPO_ROOT / "pyproject.toml", "rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def test_pyproject_version_is_semver(pyproject_version):
    assert re.fullmatch(r"\d+\.\d+\.\d+", pyproject_version), f"pyproject version {pyproject_version!r} is not x.y.z"


def test_settings_app_version_matches_pyproject(pyproject_version):
    """settings derives this — a mismatch means the derivation broke, not a stale edit."""
    assert settings.APP_VERSION == pyproject_version, (
        f"settings.APP_VERSION is {settings.APP_VERSION!r} but pyproject says "
        f"{pyproject_version!r}. If it is 'unknown', settings could not read pyproject.toml."
    )


def test_settings_app_version_is_never_unknown():
    """The loud fallback must not be what production reports."""
    assert settings.APP_VERSION != "unknown", (
        "settings could not read pyproject.toml — check it is present one level above BASE_DIR."
    )


def test_readme_badge_matches_pyproject(pyproject_version):
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    badges = re.findall(r"badge/version-v(\d+\.\d+\.\d+)", readme)
    assert badges, "no version badge found in README.md"
    assert set(badges) == {pyproject_version}, (
        f"README version badge(s) {sorted(set(badges))} != pyproject {pyproject_version!r}. "
        "Run `make version x.y.z` rather than editing pyproject.toml by hand."
    )


def test_uv_lock_matches_pyproject(pyproject_version):
    """`make version` regenerates uv.lock; a mismatch means it was not staged."""
    lock = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
    match = re.search(r'name = "five-a-day"\nversion = "([^"]+)"', lock)
    assert match, "five-a-day entry not found in uv.lock"
    assert match.group(1) == pyproject_version, (
        f"uv.lock says {match.group(1)!r} but pyproject says {pyproject_version!r}. Run `uv lock` and stage the result."
    )


def test_readme_recent_versions_table_leads_with_current(pyproject_version):
    """The Recent Versions table is prose, so nothing updates it automatically.

    It is the one place that genuinely lags a bump — `make version` prints a
    reminder and stops. Failing here is the reminder with teeth: run the
    `update-readme` skill to add the row before shipping.
    """
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\|\s*\*\*v(\d+\.\d+\.\d+)\*\*\s*\|", readme, re.MULTILINE)
    assert rows, "no bolded current-version row found in the Recent Versions table"
    assert rows[0] == pyproject_version, (
        f"Recent Versions table leads with v{rows[0]} but pyproject says {pyproject_version}. "
        "Run the `update-readme` skill to add the new row and Version History block."
    )
