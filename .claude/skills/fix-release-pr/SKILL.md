---
name: fix-release-pr
description: Use when the open testing → main release PR has merge conflicts, failing checks, or review/CodeQL comments to address. Diagnoses the conflicts, applies the real fix on development as a new patch version, merges to testing, pushes, and verifies the PR is mergeable — iterating until it is.
---

# fix-release-pr

Unblock the open `testing → main` release PR.

The fix always lands on **development** as a new patch version, then propagates
`development → testing → (PR) → main`. Never commit directly to `testing` or `main`
to fix content — that breaks the release path and silently disables the auto-merge
workflow (see **Gotchas**).

---

## Step 1 — Find the PR and read its state

```bash
git fetch origin
gh pr list --state open --base main --json number,title,headRefName,mergeable,mergeStateStatus
```

Take the PR whose `headRefName` is `testing`. If there is none, stop and tell the user —
there is nothing to unblock. (`auto-merge.yml` only opens that PR as a side effect of the
workflow doing the `development → testing` merge itself; a hand-merge never creates one.)

Record `mergeable`:
- `CONFLICTING` → go to **Step 2**.
- `MERGEABLE` + `mergeStateStatus: BLOCKED` → no conflicts; only checks/review are pending.
  Skip to **Step 3** (comments) and stop after that if there is nothing to fix.

---

## Step 2 — Reproduce the conflicts locally

Never guess from the GitHub UI. Reproduce them on a throwaway branch:

```bash
git checkout -b conflict-probe origin/testing
git merge origin/main --no-commit
git diff --name-only --diff-filter=U          # the conflicted files
```

For each conflicted file, count the hunks — this is the key diagnostic:

```bash
for f in $(git diff --name-only --diff-filter=U); do echo "$f: $(grep -c '^<<<<<<<' $f)"; done
```

Then classify. **Do this before touching anything** — the two classes have completely
different fixes and the wrong one wastes a version.

### Class A — whole-file conflict (hunk count is 1 but the hunk is the entire file)

This is almost always a **line-ending mismatch**, not real divergence. Confirm it:

```bash
git show origin/main:PATH > /tmp/m.txt
git show origin/testing:PATH > /tmp/t.txt
echo "main CR: $(tr -cd '\r' < /tmp/m.txt | wc -c)   testing CR: $(tr -cd '\r' < /tmp/t.txt | wc -c)"
```

One side `0` and the other non-zero proves it: one branch stores LF, the other CRLF, so Git
reads every line as modified on both sides at once. Fix per **Step 4a**.

> Write to temp files. `diff <(git show …) <(git show …)` process substitution is unreliable
> in Git Bash on Windows and reports whole-file differences that are not real — that false
> signal cost a full diagnostic detour in v1.15.2.

### Class B — small, localised hunks

Genuine content divergence: `main` holds the older text, `testing` the newer. Fix per **Step 4b**.

Clean up the probe before continuing:

```bash
git merge --abort && git checkout development && git branch -D conflict-probe
```

---

## Step 3 — Read the PR comments

Inline review + CodeQL comments live on a different endpoint than issue comments. Check both:

```bash
gh api repos/{owner}/{repo}/pulls/NUMBER/comments --jq '.[] | "### \(.path):\(.line // .original_line)\n\(.body)\n"'
gh api repos/{owner}/{repo}/issues/NUMBER/comments --jq '.[] | "=== \(.user.login) ===\n\(.body)\n"'
```

**Assess every finding against the source before acting. Do not bulk-apply.** CodeQL has known
false positives in this codebase, and "fixing" them breaks the app:

- **"Unused global variable" on a Django setting** (`CACHES`, and anything else in
  `settings.py`) — FALSE POSITIVE. Settings are consumed by Django's settings machinery, not
  by the module. Deleting `CACHES` silently drops the Redis cache and makes the rate limiter
  per-process again.
- **"Unused global variable" on a module global assigned under `global`** (e.g.
  `_quotes_retry_after` in `core/views/dashboard.py`) — check whether it is *read* elsewhere in
  the function. It is read at the top of `_get_quote()`; removing it kills the API backoff.
- **"Assert statement has a side-effect"** — GENUINE, and worth fixing. Under `python -O`
  assertions are stripped and the side-effecting call goes with them. Hoist the call to its own
  statement and assert on the result.

Record which findings you actioned and which you rejected **and why** — that goes in the commit
message. Silently ignoring a scanner finding is what makes the next person re-litigate it.

---

## Step 4 — Apply the fix on development

```bash
git checkout development && git pull --ff-only origin development
```

### Step 4a — Line-ending fix (Class A)

Ensure `.gitattributes` normalises everything, not just `*.sh`:

```
* text=auto eol=lf
*.sh text eol=lf
*.png binary
*.jpg binary
*.jpeg binary
*.gif binary
*.ico binary
*.pdf binary
*.woff binary
*.woff2 binary
```

`text=auto` lets Git detect binary by NUL bytes; the explicit `binary` lines are belt-and-braces
so auto-detection can never rewrite an image. Then renormalise and **prove it is content-neutral**:

```bash
git add --renormalize .
git diff --cached --ignore-cr-at-eol --stat
```

That second command must show **only** the files you deliberately edited. If it lists a file you
did not touch, the renormalisation changed real content — stop and investigate.

Verify nothing but binaries still carry CR:

```bash
git ls-files | while read f; do
  if git show ":$f" 2>/dev/null | tr -cd '\r' | head -c1 | grep -q .; then echo "$f"; fi
done
```

Only `.png` / `.ico` / other binaries may remain — those bytes are image data.

### Step 4b — Content conflicts (Class B)

These resolve on `testing` in **Step 6**, not here. `testing` is where `main`'s content already
lives, so that is where the merge base has to advance.

**Do NOT merge `main` into `development` to fix this.** `development` has never merged `main`, so
that produces ~45 conflicts across the whole tree for no benefit. Verified dead end in v1.15.2.

---

## Step 5 — Bump the patch version and commit

Bump `X.Y.Z` → `X.Y.(Z+1)` in **all four** places:

```bash
sed -i 's/^version = ".*"/version = "NEW"/' pyproject.toml
sed -i 's/APP_VERSION = os.getenv("APP_VERSION", ".*")/APP_VERSION = os.getenv("APP_VERSION", "NEW")/' project/project/settings.py
sed -i -E 's|version-v[0-9]+(\.[0-9]+)*-brightgreen|version-vNEW-brightgreen|' README.md
uv lock --quiet
```

Also update `README.md`:
- **Recent Versions** table — add the new row at the top, drop the oldest, keep exactly 3.
  Description ≤10 words.
- **Version History** — add `<details id="vXYZ" open>` for the new version and remove `open`
  from the previous block.

Verify, then commit:

```bash
make lint
make test            # requires Docker; run `make up` first if it errors on connection
git add -A
git status --porcelain          # review the staged set before committing
```

A line-ending renormalisation stages a lot of files. Confirm the set is only modifications
(`M`) — any unexpected new file is a red flag worth opening before you commit.

Write the commit message to a file and use `-F`. `git commit -m @'…'@` is PowerShell
here-string syntax and the Bash tool takes it literally, leaving a stray `@` in the subject;
`git merge -F -` cannot read stdin at all.

```bash
cat > /tmp/cmsg.txt <<'EOF'
vX.Y.Z — <headline>

<what was actually wrong and why the fix is the fix>

<which review/CodeQL findings were actioned, and which were rejected as false
positives with the reason>
EOF
git commit -F /tmp/cmsg.txt
git push origin development
```

---

## Step 6 — Propagate to testing

```bash
git checkout testing && git pull --ff-only origin testing
cat > /tmp/m1.txt <<'EOF'
vX.Y.Z — <same headline>

Brings testing in sync with development at vX.Y.Z.
EOF
git merge development --no-ff -F /tmp/m1.txt
```

### If Class B conflicts remain — record main as merged

Check first whether `main` actually carries anything `testing` lacks. `main`'s HEAD is typically
a **squash** of the dev line, so its tree is often byte-identical to a commit already in testing's
history — meaning it brings nothing and only *looks* divergent:

```bash
git diff --stat origin/main <candidate-sha>     # empty output = identical trees
git merge-base --is-ancestor <candidate-sha> origin/testing && echo "already in testing"
```

Find the candidate by matching subject lines (`git log origin/testing --format='%h %s'` against
`git log origin/main -1 --format='%s'`).

**Only if both checks pass** — identical tree AND already an ancestor of `testing` — record the
merge without touching files:

```bash
TREE_BEFORE=$(git rev-parse HEAD^{tree})
git merge -s ours origin/main -F /tmp/m2.txt
TREE_AFTER=$(git rev-parse HEAD^{tree})
[ "$TREE_BEFORE" = "$TREE_AFTER" ] && echo "VERIFIED: tree unchanged" || echo "WARNING: tree changed"
```

The tree hashes **must** match. `-s ours` discards the other side's changes entirely, so it is
only safe once you have proven there are none to discard. If the checks do not pass, resolve the
conflicts by hand instead, taking testing's newer content, and spot-check that every fix `main`
carried is still present (e.g. `--chdir project` in the `Dockerfile`, `SESSION_COOKIE_SAMESITE`
defaulting to `Lax`).

---

## Step 7 — Verify before pushing

Simulate the PR merge locally rather than pushing and hoping:

```bash
git checkout -b final-check origin/main
git merge testing --no-commit
echo "conflicts: $(git diff --name-only --diff-filter=U | wc -l)"
```

Must be `0`. Sanity-check the resulting tree (version bumped, production fixes intact), then:

```bash
git merge --abort; git checkout testing; git branch -D final-check
make test
git push origin testing
```

---

## Step 8 — Confirm and iterate

```bash
gh pr view NUMBER --json mergeable,mergeStateStatus,title
```

- `MERGEABLE` → done. `BLOCKED` alongside it is normal: CI + review are still pending.
- `CONFLICTING` → **return to Step 2** and iterate with another patch version. Something was
  misdiagnosed; re-classify the conflicts rather than reapplying the same fix.

Report to the user:
- What actually caused the conflicts (root cause, not symptom)
- The new version and what it changed
- Which review findings were actioned vs rejected, with reasons
- Whether the PR title still matches the shipped version — the title is written from the
  version at PR-creation time and goes stale after a patch bump. Offer to retitle:
  `gh pr edit NUMBER --title "…"`

---

## General rules

- **The fix goes on `development`.** `testing` only ever receives merges. The one exception is
  the `-s ours` ancestry record in Step 6, which changes no files.
- **Prove content-neutrality before trusting a mechanical fix** — `--ignore-cr-at-eol` for
  renormalisation, tree-hash comparison for `-s ours`. Both are cheap; both caught real
  ambiguity in v1.15.2.
- **Never bulk-apply scanner suggestions.** Assess each against the source; state the rejects.
- **Use `make test`**, which runs against Docker PostgreSQL. Never `pytest` directly or
  `TEST_DB_ENGINE=sqlite`.
- **Write multi-line messages to a file and use `-F`** — not `-m` with a here-string.
- Before any `git checkout`/`reset`/`clean`, run `git status` and stash anything uncommitted.
- Clean up probe branches (`conflict-probe`, `final-check`) even when a step fails.
