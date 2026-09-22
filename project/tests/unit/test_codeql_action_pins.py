"""Every `github/codeql-action/*` step must be pinned to ONE shared SHA.

The CodeQL actions write and read a state file that carries their version, so a
run whose `init` is v4.38.1 and whose `analyze` is v4.38.0 fails outright with
"Loaded a configuration file for version '4.38.1', but running version
'4.38.0'". No analysis happens; the Security tab simply stops being updated.

This has now happened TWICE, both times because Dependabot bumps each
`github/codeql-action/<step>` as its own dependency and lands them in separate
PRs. PR #53 took `init` to v4.37.9 and left `autobuild`/`analyze` on v3, and
v1.31.1 took `init`, `autobuild` and `upload-sarif` to v4.38.1 while `analyze`
had no PR at all. Both times the rule was written down in CLAUDE.md and nothing
enforced it, so the gap only surfaced as a red run after the merge.

Enforced here rather than in the workflow because the workflow cannot see
itself: a mismatched pin breaks the very job that would have to do the
checking. A unit test is also where somebody finds out BEFORE pushing, which is
the whole difference between this and reading the failure in Actions.

Scoped to the full tree (every workflow file), because the steps are spread
across three of them — codeql.yml runs the analysis while ci.yml and
scorecard.yml upload SARIF, and an upload step from a different version is the
same class of mismatch.
"""

import re
from pathlib import Path

import pytest
from django.conf import settings as dj_settings

WORKFLOWS_DIR = Path(dj_settings.BASE_DIR).parent / ".github" / "workflows"

#: `uses: github/codeql-action/<step>@<sha>  # <version comment>`
_PIN = re.compile(
    r"uses:\s*github/codeql-action/(?P<step>[\w-]+)@(?P<sha>[0-9a-f]{40})\s*#\s*(?P<version>\S+)",
)


def _pins():
    """Every codeql-action pin in the tree as (file, step, sha, version)."""
    found = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        for match in _PIN.finditer(path.read_text(encoding="utf-8")):
            found.append((path.name, match["step"], match["sha"], match["version"]))
    return found


@pytest.fixture(scope="module")
def pins():
    found = _pins()
    # Guards the regex, not the workflows: a markup change that stopped this
    # matching would leave every assertion below vacuously true, which is the
    # failure mode a pinning test can least afford.
    assert found, f"parsed no codeql-action pins out of {WORKFLOWS_DIR} — has the `uses:` syntax changed?"
    return found


def test_every_codeql_action_step_shares_one_sha(pins):
    by_sha: dict[str, list[str]] = {}
    for filename, step, sha, _version in pins:
        by_sha.setdefault(sha, []).append(f"{filename}:{step}")

    assert len(by_sha) == 1, (
        "github/codeql-action steps are pinned to different SHAs, which fails every "
        f"CodeQL run with a version-mismatch error: { {sha: steps for sha, steps in by_sha.items()} }"
    )


def test_every_codeql_action_step_names_one_version(pins):
    """The comments must agree too, and for a reason beyond tidiness.

    Dependabot only rewrites a SHA pin whose comment carries an EXACT version,
    and it decides what to bump from that comment. Two steps claiming different
    versions is therefore both a symptom of drift and the mechanism by which
    the next bump recreates it.
    """
    versions = {version for _f, _s, _sha, version in pins}
    assert len(versions) == 1, f"codeql-action pins name more than one version: {sorted(versions)}"


def test_the_version_comment_is_exact_and_not_a_bare_major(pins):
    """`# v4` freezes a line against Dependabot; `# v4.38.1` does not.

    A bare-major comment is why `init` could advance while its siblings stood
    still: Dependabot skips the lines it cannot resolve to an exact version,
    and skipping is silent.
    """
    for filename, step, _sha, version in pins:
        assert re.fullmatch(r"v\d+\.\d+\.\d+", version), (
            f"{filename}:{step} is pinned with the comment '{version}'. It must name an exact "
            "version (e.g. v4.38.1) or Dependabot will never bump that line."
        )
