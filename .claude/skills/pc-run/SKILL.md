---
name: pc-run
description: Use when the user wants to run pre-commit checks, fix any issues automatically, and optionally bump the project version. Runs pre-commit in a loop until all hooks pass cleanly, then asks the user whether this is a new version and applies it if so.
---

# pc-run

Run pre-commit on all files, fix every failure, loop until clean, then handle version bumping.

---

## Step 1 — Run pre-commit

```bash
uv run --no-project pre-commit run --all-files
```

Capture the full output. If it exits 0 (all hooks passed), jump to **Step 3**.

---

## Step 2 — Fix failures and re-run

Read every error in the output and fix the root cause. Rules:

- **Never suppress with `# noqa`** unless the rule is genuinely inapplicable to that line (e.g. `PLW0603` on a legitimate module-level global). If you add a `# noqa`, explain why in a comment.
- **Ruff E402** — import is not at the top of the file. Move it to the top. If it is after a `pytestmark` or section comment, move it above those too.
- **Ruff F811** — duplicate class/function name. Rename the second definition (add a suffix like `Extra`, or merge the methods into the first class).
- **Ruff F841** — local variable assigned but never used. Remove the `as <name>` binding, or delete the variable entirely.
- **Ruff B017** — `pytest.raises(Exception)` is too broad. Use the specific exception the code raises (check the source with Grep). If the source raises `Exception`, change it to `RuntimeError` in the source and update the test.
- **Ruff I001** — import order. Run `uv run --no-project ruff check --fix project/` then re-check.
- **Ruff UP/B/W/E/F** — follow the lint message. Most are auto-fixable; run `uv run --no-project ruff check --fix project/` first, then fix any remaining ones manually.
- **mypy `no-redef`** — same as F811 above; rename the duplicate.
- **mypy other errors** — fix the type error in the source. Do not add `# type: ignore` unless the error is a known false positive from a missing stub.
- **bandit** — review the finding. Fix the security issue if real. Add `# nosec B<id>` with a comment only if it is provably a false positive.
- **pytest-coverage** — coverage dropped below 75%. Run `make test` inside Docker to see which lines are missing, then add tests. Do not lower `fail_under`.

After each batch of fixes, go back to **Step 1**. Keep iterating until all hooks pass.

---

## Step 3 — All hooks passed

Read the current version from `pyproject.toml`, then ask:

> Pre-commit passed. Current version is X.Y.Z.
> Is this a new version?
> - Reply `y` to bump the patch (X.Y.Z+1)
> - Reply a specific version like `A.B.C` to set it directly
> - Reply `n` to skip

---

## Step 4 — Apply the version bump (if requested)

**Determine the new version:**
- If the user replied `y`: read `version = "X.Y.Z"` from `pyproject.toml`, split on `.`, increment the last segment by 1, construct `X.Y.(Z+1)`.
- If the user replied a version string (e.g. `1.3.0`): use that directly.
- If the user replied `n` or anything else: do nothing and stop.

**Apply the bump** (substitute the real new version for `NEW` below):

```bash
sed -i 's/^version = ".*"/version = "NEW"/' pyproject.toml
sed -i 's/APP_VERSION = os.getenv("APP_VERSION", ".*")/APP_VERSION = os.getenv("APP_VERSION", "NEW")/' project/project/settings.py
sed -i -E 's|version-v[0-9]+(\.[0-9]+)*-brightgreen|version-vNEW-brightgreen|' README.md
uv lock --quiet
```

**Stage `uv.lock` if it changed:**

```bash
git status --porcelain uv.lock
# if modified:
git add uv.lock
```

**Tell the user:**

> Version bumped from X.Y.Z to NEW in pyproject.toml, settings.py, and the README badge. uv.lock regenerated and staged.
> The Recent Versions table and Version History block in README.md were NOT updated — run `/update-readme` to refresh them.

---

## General rules

- Run pre-commit with `uv run --no-project pre-commit run --all-files`, not `make pc-run` (the Makefile target is interactive).
- Do not commit. The skill stops at "ready to commit" — the user decides when to `git commit`.
- Do not touch `TOMORROW.md` or any file the failing hooks did not flag.
