---
name: sync-branches
description: Use when main, testing and development have drifted apart — typically right after a testing → main release PR is merged, which leaves testing and development behind main. Stashes any work in progress, fast-forwards all three branches from origin, merges main into testing and development, pushes both, then returns to development and restores the stashed work fully staged. Triggers on "sync branches", "sync with main", "testing and development are behind", "/sync-branches".
---

# sync-branches

Bring `testing` and `development` back up to `main`, without losing whatever the user
is in the middle of.

**Why this exists.** Every release travels `development → testing → (PR) → main`. The
PR merge lands a commit on `main` that neither `testing` nor `development` has, so the
moment a release ships both branches are behind and the *next* release PR opens
**BEHIND** before anybody has written a line of code. Merging `main` back down closes
that gap immediately instead of at the start of the next release.

**End state:** `main` is an ancestor of both `testing` and `development`, both are
pushed, HEAD is on `development`, and the user's work in progress is back in the
working tree and **fully staged**.

---

## Invariants — read before running anything

These are the properties that make the skill safe to run on a dirty tree. Do not relax
them for convenience.

- **Never `git reset --hard`, never `git checkout -B` an existing branch, never
  `git push --force`, never `git clean`.** Every branch update here is a fast-forward
  or a merge. Nothing in this skill can destroy an unpushed commit.
- **Never push `main`.** This skill only ever *reads* main. If local `main` has commits
  `origin/main` does not, that is a repo-state error — stop and ask.
- **Never pop a stash you did not create.** `git stash push` on a clean tree exits **0**
  and prints "No local changes to save" without creating anything, so a later blind
  `git stash pop` would restore somebody's *older*, unrelated stash on top of the user's
  branch. Always resolve the stash by its unique message (Step 5).
- **Never push a branch whose merge left conflict markers.** Confirm
  `git diff --name-only --diff-filter=U` is empty first.
- **Never stash ignored files.** `git add -A` respects `.gitignore`, so `.env`,
  `staticfiles/` and the rest stay put. Do not reach for `git stash -a`.
- **If any merge needed MANUAL conflict resolution, run `make test` before pushing.**
  A clean fast-forward or a clean auto-merge does not need it.

---

## Step 1 — Probe, don't act

Read the full picture before touching anything. One command:

```bash
git fetch origin --prune
echo "HEAD: $(git rev-parse --abbrev-ref HEAD)"
echo "--- working tree ---"
git status --porcelain
echo "--- branches ---"
for b in main testing development; do
  if git rev-parse --verify --quiet "refs/heads/$b" >/dev/null; then
    L=$(git rev-parse "$b"); R=$(git rev-parse "origin/$b"); B=$(git merge-base "$b" "origin/$b")
    if   [ "$L" = "$R" ]; then s="up to date"
    elif [ "$L" = "$B" ]; then s="behind by $(git rev-list --count $b..origin/$b) (fast-forwardable)"
    elif [ "$R" = "$B" ]; then s="AHEAD by $(git rev-list --count origin/$b..$b) — unpushed local commits"
    else s="DIVERGED +$(git rev-list --count origin/$b..$b)/-$(git rev-list --count $b..origin/$b)"
    fi
  else s="no local branch — will be created tracking origin/$b"
  fi
  echo "  $b: $s"
done
echo "--- gap to close ---"
for b in testing development; do
  git merge-base --is-ancestor origin/main "origin/$b" 2>/dev/null \
    && echo "  origin/$b already contains main" \
    || echo "  origin/$b is missing $(git rev-list --count origin/$b..origin/main) commit(s) from main"
done
```

Then decide:

- **Both already contain main and the tree is clean** → nothing to do. Say so and stop.
  Do not manufacture empty merge commits to make the run look productive.
- **`main` is AHEAD or DIVERGED** → **stop and ask.** Local commits on `main` should not
  exist; something merged directly to main. Do not try to fix it here — that is
  `/solve-main-conflicts` territory, or a conversation.
- **`testing` or `development` DIVERGED** → that local branch has unpushed commits *and*
  the remote moved. Report both counts and ask before continuing: the merge will work,
  but the user needs to know their local commits are about to be pushed.
- **Otherwise** → continue.

---

## Step 1b — Check the stash for collisions BEFORE creating it

The stash is this skill's one genuinely dangerous moment: it is popped in Step 5 on top
of a tree that `main` has just been merged into, so if the work in progress touches the
same files `main` changed, the pop conflicts — with the user's uncommitted work as one
side of it. The larger the work in progress, the likelier that is.

```bash
comm -12 <(git diff --name-only development...origin/main | sort)          <(git status --porcelain --untracked-files=no | awk '{print $2}' | sort)
```

- **Empty** → carry on to Step 2, nothing to think about.
- **Non-empty** → say so and offer the choice before stashing. Both ways out are fine
  and neither is yours to pick:
  - **Commit first, then sync.** The work becomes a real commit, `git merge origin/main`
    resolves in a proper merge with both sides committed, and nothing is ever stashed.
    This is usually the right answer for a release-sized change.
  - **Defer the sync.** If `main` carries no version change
    (`git diff development...origin/main -- pyproject.toml | grep '^[-+]version'` is
    empty), the merge can simply happen after the user's next commit. `update-readme`'s
    Step 0 does exactly this and documents why.

Whichever is chosen, snapshot the index first — a staged-but-uncommitted tree has no
commit behind it:

```bash
git tag -f snapshot-pre-sync $(git commit-tree $(git write-tree) -p HEAD -m "SNAPSHOT: before sync-branches")
git diff --stat snapshot-pre-sync --cached    # empty = the snapshot holds the index exactly
```

---

## Step 2 — Stash the work in progress

Only if `git status --porcelain` was non-empty in Step 1. Otherwise skip to Step 3 and
record "nothing stashed".

Everything is staged first — that is what lets Step 5 restore it staged without having
to remember which files were staged before.

```bash
TAG="sync-branches $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "$TAG"
git add -A
git stash push -m "$TAG"
git stash list --format='%gd  %gs' | head -3
git status --porcelain        # must now print nothing
```

**Write the exact `$TAG` string into your working notes.** Shell variables do not
survive between tool calls, and Step 5 resolves the stash by that message. Confirm the
new entry appears in `git stash list` **and** that `git status --porcelain` prints
nothing before going on — if the stash was not created, every step after this is
operating on a tree you have not saved.

Untracked files are covered: `git add -A` puts them in the index, so `git stash push`
takes them with it. Ignored files are not, and must not be.

---

## Step 3 — Fast-forward all three branches from origin

`origin` was already fetched in Step 1. Update each local branch to its remote as a
**fast-forward only**, so a diverged branch fails loudly instead of quietly growing a
merge nobody asked for.

```bash
for b in main testing development; do
  if git rev-parse --verify --quiet "refs/heads/$b" >/dev/null; then
    git checkout "$b" || exit 1
    git merge --ff-only "origin/$b" || echo "!! $b could not fast-forward — see the Step 1 verdict"
  else
    git checkout -b "$b" --track "origin/$b" || exit 1
  fi
done
git checkout main && git rev-parse --short HEAD main origin/main
```

A branch reported AHEAD in Step 1 prints "Already up to date" here rather than failing —
correct, there is nothing to pull. A branch reported DIVERGED fails the `--ff-only`;
leave it, the merge in Step 4 handles it.

`main` must now equal `origin/main`. If it does not, stop.

---

## Step 4 — Merge main into testing and development, then push

One branch at a time, and **do not push a branch until its merge is clean**.

```bash
git checkout testing
git merge main --no-edit -m "Sync: merge main into testing"
git diff --name-only --diff-filter=U      # must be empty
```

Conflicts → **Conflict playbook** below. Clean → push:

```bash
git push origin testing
```

Then the same for `development`:

```bash
git checkout development
git merge main --no-edit -m "Sync: merge main into development"
git diff --name-only --diff-filter=U      # must be empty
git push origin development
```

Three things about these merges:

- **Do not put a `vX.Y.Z` in the subject.** `auto-merge.yml` has a gate that fails
  LOUDLY when the last development commit names a version disagreeing with
  `pyproject.toml`. A sync merge is not a release; keep the version out of it.
- If `testing` is already an ancestor of `main` — the normal case straight after a
  release PR merges — this **fast-forwards and creates no commit at all**. That is the
  desired outcome, not something to force into a merge commit with `--no-ff`.
- A push rejected as non-fast-forward means the remote moved while you were working.
  Re-run Steps 1 and 3 for that branch. Never force.

---

## Step 5 — Return to development and restore the work, staged

Skip if Step 2 recorded "nothing stashed" — but say so in the report.

Resolve the stash **by the message you recorded**, never by `stash@{0}`:

```bash
git checkout development
git stash list --format='%gd  %gs'
```

Find the entry whose message is your `TAG` and use that ref:

```bash
git stash pop 'stash@{N}'
git diff --name-only --diff-filter=U      # any conflicts?
```

- **No conflicts** → stage everything, which is the requested end state:

  ```bash
  git add -A
  git status --short
  ```

  `git stash pop --index` is deliberately *not* used. Step 2 ran `git add -A`, so the
  stash's index tree and worktree tree are identical — a plain pop followed by
  `git add -A` reproduces exactly the same staged state, and it avoids the way
  `--index` fails when a restored file was also touched by the merge.

- **Conflicts** → resolve them first (playbook below). **Do not run `git add -A` while
  unmerged paths exist** — it would stage the conflict markers as though they were
  content, and the result looks fine in `git status`. Stage each file as you finish it,
  then `git add -A` for the remainder.

A failed pop leaves the stash in place; that is the safety net. Do not `git stash drop`
anything — a successful `pop` already removed it, and a failed one should survive until
the user agrees it is safe to discard.

---

## Step 6 — Verify

The success criterion is ancestry, not "the commands ran without error":

```bash
git rev-parse --abbrev-ref HEAD            # development
for b in testing development; do
  git merge-base --is-ancestor main "$b" && echo "$b contains main OK" || echo "$b MISSING main"
  git merge-base --is-ancestor "origin/$b" "$b" && echo "$b pushed OK" || echo "$b NOT pushed"
done
git rev-list --count development..testing  # commits testing has that development lacks
git status --short
```

- Both branches must contain `main` and match their remote.
- `development..testing` should be `0`. If it is not, `testing` carries something
  `development` does not — this skill deliberately does **not** merge testing into
  development, so surface it and let the user decide.
- `git status --short` should show every restored file with a staged status (`A`/`M` in
  the first column) and nothing unmerged.

---

## Conflict playbook

Sync merges conflict in a small, predictable set of files — the ones that change in
*every* release. Handle these mechanically:

| File | Resolution |
|------|------------|
| `pyproject.toml` | Take the **higher** version. `development` leads the release path, so its version is the live one. Never resolve to main's. |
| `uv.lock` | Do not hand-merge and do not pick a side. Resolve `pyproject.toml` first, then regenerate: `uv lock`, then `git add uv.lock`. |
| `README.md` | Development's copy is normally a superset (it carries the newer version rows). Take development's, then re-check the badge, the 3-row Recent Versions table and the Version History `open` attribute against `pyproject.toml`. |
| `CLAUDE.md`, `project/*/README.md` | Both sides usually **added** prose in different places. Keep both additions; only choose a side when the two genuinely restate the same rule. |
| `project/project/settings.py` | Almost always two independent additions — keep both. If both sides touched the *same* setting, stop and ask: that is a real disagreement, not a merge artefact. |

After resolving, run the coherence guard before pushing. It takes milliseconds and
catches exactly the drift these conflicts cause:

```bash
python scripts/check_version_coherence.py
```

**Anything outside that table, or any conflict where both sides changed the same logic:
stop and ask the user.** Show them the conflicted hunk and what each side was trying to
do. Guessing at a semantic conflict in a sync merge is how a fix silently disappears —
and because this merge is pushed immediately, a wrong guess reaches the shared branch
before anyone reviews it.

Recovery: `git merge --abort` restores the branch and leaves the Step 2 stash untouched.
If a stash pop goes badly, `git checkout -- <file>` on the conflicted paths and re-pop —
the stash survives a failed pop.

---

## Step 7 — Report back

Under 15 lines:

- Which branches moved, and by how many commits
- Whether each merge fast-forwarded or created a merge commit
- Any conflicts, and how each was resolved
- Whether the stash was restored, and how many files are now staged
- Anything you stopped on that needs the user to decide
- Whether `make test` was run (say so if a manual resolution made it necessary)

---

## Gotchas

- **A sync merge on `development` resets the auto-merge soak clock.** `auto-merge.yml`
  requires the last commit on development to be **at least 3 h old**. Pushing a merge
  commit here restarts that timer, so syncing immediately before a release delays the
  `development → testing` auto-merge by three hours. Mention it if the user looks like
  they are mid-release.
- **This skill never merges `testing` into `development`.** The release path is
  one-directional; the only thing that flows back down is `main`. If `testing` has
  commits development lacks (Step 6 reports it), something bypassed development and a
  human needs to look at it.
- **Pushing `development` and `testing` triggers CI** (tests, `pip-audit`, CodeQL) and
  can re-trigger the production deploy watchdog. Expected and harmless — but do not read
  a red run on the sync push as caused by the user's stashed work, which was never
  committed.
- **The stash is the only copy of the user's work for the duration of Steps 3–5.** If
  something goes wrong in the middle, recovery is `git stash list` plus the recorded
  tag — tell the user the tag in that situation rather than silently retrying.
