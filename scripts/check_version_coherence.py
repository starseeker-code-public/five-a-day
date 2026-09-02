"""Pre-commit guard: the app version must be coherent everywhere it appears.

`pyproject.toml` is the single source of truth. Two tools update the copies and
this hook verifies BOTH were actually run:

- `make version x.y.z` (or the `make pc-run` auto-bump) rewrites the README
  badge and regenerates `uv.lock`.
- the `/update-readme` skill adds the Recent Versions row and the Version
  History `<details>` block — prose that nothing updates automatically, so it
  is the part that genuinely lags a bump.

`project/tests/unit/test_version_consistency.py` enforces most of the same
rules, but only inside the Docker test suite; this hook fails in seconds,
without Docker, and additionally checks the Version History block. Stdlib
only, on purpose — `uv run --no-project` must stay enough to run it.
"""

import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

RECENT_VERSIONS_ROWS = 3  # README rule: the table keeps only the last 3 versions

FIX_MAKE_VERSION = "run `make version x.y.z` instead of editing pyproject.toml by hand"
FIX_UPDATE_README = "run the /update-readme skill to refresh the README for the new version"


def check(errors: list[str]) -> str:
    with open(REPO_ROOT / "pyproject.toml", "rb") as handle:
        version = tomllib.load(handle)["project"]["version"]

    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        errors.append(f"pyproject.toml version {version!r} is not x.y.z semver")
        return version

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    # -- `make version` outputs ---------------------------------------------
    badges = set(re.findall(r"badge/version-v(\d+\.\d+\.\d+)", readme))
    if not badges:
        errors.append("no version badge found in README.md")
    elif badges != {version}:
        errors.append(f"README badge says {sorted(badges)} but pyproject says {version} -- {FIX_MAKE_VERSION}")

    lock = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
    lock_match = re.search(r'name = "five-a-day"\nversion = "([^"]+)"', lock)
    if not lock_match:
        errors.append("five-a-day entry not found in uv.lock")
    elif lock_match.group(1) != version:
        errors.append(f"uv.lock says {lock_match.group(1)} but pyproject says {version} -- run `uv lock` and stage it")

    # -- /update-readme outputs ---------------------------------------------
    # Recent Versions table: current version bolded on the lead row, 3 rows total.
    table = re.search(r"^\| Version \| Date \| Description \|\n\|[-| ]+\|\n((?:\|.*\n)+)", readme, re.MULTILINE)
    if not table:
        errors.append("Recent Versions table not found in README.md")
    else:
        rows = table.group(1).strip().splitlines()
        lead = re.match(r"\|\s*\*\*v(\d+\.\d+\.\d+)\*\*\s*\|", rows[0])
        if not lead:
            errors.append(f"Recent Versions lead row is not a bolded version: {rows[0]!r} -- {FIX_UPDATE_README}")
        elif lead.group(1) != version:
            errors.append(
                f"Recent Versions table leads with v{lead.group(1)} but pyproject says {version} -- {FIX_UPDATE_README}"
            )
        if len(rows) != RECENT_VERSIONS_ROWS:
            errors.append(
                f"Recent Versions table has {len(rows)} rows, must keep exactly the last"
                f" {RECENT_VERSIONS_ROWS} -- {FIX_UPDATE_README}"
            )

    # Version History: a <details id="vXYZ" open> block for the current version,
    # and no other block still carrying the `open` attribute.
    anchor = "v" + version.replace(".", "")
    open_ids = re.findall(r"<details id=\"(v\d+)\" open>", readme)
    if f'<details id="{anchor}"' not in readme:
        errors.append(f'no Version History <details id="{anchor}"> block for v{version} -- {FIX_UPDATE_README}')
    elif anchor not in open_ids:
        errors.append(f"Version History block {anchor} exists but is not marked `open` -- {FIX_UPDATE_README}")
    stale_open = [block_id for block_id in open_ids if block_id != anchor]
    if stale_open:
        errors.append(
            f"previous Version History block(s) still marked `open`: {', '.join(stale_open)} -- {FIX_UPDATE_README}"
        )

    return version


def main() -> int:
    errors: list[str] = []
    version = check(errors)
    if errors:
        print(f"Version coherence FAILED (pyproject.toml says {version}):")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"Version coherence OK: v{version} in pyproject, README badge, uv.lock, Recent Versions, Version History")
    return 0


if __name__ == "__main__":
    sys.exit(main())
