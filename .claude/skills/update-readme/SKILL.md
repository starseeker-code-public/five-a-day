---
name: update-readme
description: Use when the user says their work is done and they want the project documentation updated to reflect the staged changes. Despite the name, this skill updates the full documentation set — the top-level README.md, every per-app README, CLAUDE.md, DEPLOYMENT.md, and every file under docs/. Levels development with main FIRST — usually via the sync-branches skill, or by deferring that merge when the staged work collides with it — so the version the docs are written against is the real one, then inspects the staged diff, routes changes to the correct docs, applies per-file checklists, and sweeps for stale references across all of them.
---

# update-readme

The user invokes this skill when they have finished a unit of work and want the documentation brought up to date with what's in the staged area.

**Scope.** This skill touches EVERY documentation file in the repo — not just `README.md`. The name is historical; treat it as `update-docs`. The documentation set is:

| File | Authoritative for |
|------|-------------------|
| `README.md` (root) | Project overview, status, version history, tech stack, directory layout, testing, security, CI/CD, QA, contributing |
| `CLAUDE.md` | Rules and conventions future AI agents must follow. Gotchas. README-maintenance checklist. |
| `DEPLOYMENT.md` | GCP deployment — all 3 environments, costs, commands, free tier notes |
| `docs/GITHUB.md` | Full CI/CD reference — workflows, branch protection, secrets, public-repo hardening |
| `docs/HTTPS.md` | HTTPS setup (Docker Nginx + Cloud Run) |
| `docs/UV.md` | UV package manager guide |
| `docs/CELERY.md` | Celery worker/beat reference |
| `docs/TODO.md` | Open tasks log |
| `project/core/README.md` | Models, views, URLs, middleware, templates/static for the `core` app |
| `project/students/README.md` | Models, forms, admin, URLs for the `students` app |
| `project/billing/README.md` | Models, services, constants, exports, admin, URLs for the `billing` app |
| `project/comms/README.md` | EmailService, email functions, Celery tasks, management commands for `comms` |
| `project/ENROLLMENT_PAYMENT_SYSTEM.md` | Canonical enrollment/payment business rules (pricing, discounts, schedule) |

**`.env.example` does not exist in this repo — do not create it.** The `.env` template lives inline in `README.md` under the heading **`.env template`** (a fenced `bash` code block inside the Development & Docker section). Every time you edit the app's env var surface, update that code block.

Everything under `.venv/`, `.pytest_cache/`, `node_modules/`, `.git/` is **out of scope**. You **may read `.env`** solely to extract variable names and section comments when the user explicitly asks you to refresh the README's `.env template` code block — strip every value before writing anything user-visible. Never read `.env.testing` or any other `.env*`.

---

## Step 0 — Get `development` level with `main` FIRST

Documentation is written against a version, and the version lives in files —
`pyproject.toml`, the README badge, the Recent Versions table, `uv.lock` — that change
in *every* release and are exactly the files a stale branch disagrees about. Writing
the docs on a `development` that is behind `main` means writing them against last
release's numbers, and the mistake only surfaces later as a conflicted release PR.

The usual way to do that is the **`sync-branches`** skill
(`.claude/skills/sync-branches/SKILL.md`), which stashes the work in progress,
fast-forwards `main`, `testing` and `development` from origin, merges `main` into
`testing` and `development`, pushes both, and returns to `development` with the stash
restored **staged** — which is precisely the input Step 1 reads.

**But it is not always the right way, and running it blind can cost you the working
tree.** Its stash-and-pop conflicts when the staged work touches the same files `main`
changed. Decide with the three commands below before running anything.

### 0a. Snapshot, then measure

Always snapshot first. A staged-but-uncommitted tree has no commit behind it, and every
branch operation from here can lose it:

```bash
git tag -f snapshot-pre-docs $(git commit-tree $(git write-tree) -p HEAD -m "SNAPSHOT: staged work before docs sync")
git diff --stat snapshot-pre-docs --cached      # empty output = the snapshot holds the index exactly
```

Then measure the two things that decide the route:

```bash
git fetch --all --quiet

# 1. Does main carry commits development lacks?
git log --oneline development..origin/main

# 2. Do any of them touch a file you have staged? (the stash-pop hazard)
comm -12 <(git diff --name-only development...origin/main | sort)          <(git diff --cached --name-only | sort)

# 3. Does main carry a VERSION development lacks? (the reason this step exists)
git diff development...origin/main -- pyproject.toml | grep '^[-+]version'
```

### 0b. Route

| main is ahead? | Overlap with staged files? | Version moved on main? | Do this |
|---|---|---|---|
| No | — | — | Nothing to sync. Go to Step 1. |
| Yes | No | Either | Run **`sync-branches`** end to end, as before. |
| Yes | **Yes** | **No** | **Do NOT stash.** Document now, commit, then merge `main` down explicitly afterwards (see 0c). |
| Yes | **Yes** | **Yes** | Stop and tell the user. Documenting against the wrong version and popping a conflicting stash are both bad; the way out is theirs to choose (usually: commit the staged work first, then merge, then document). |

The third row is the common one when the release is large. It is safe precisely because
the condition that makes the sync *mandatory* — a version on `main` that `development`
lacks — is absent: hotfixes to workflows, CVE bumps in `uv.lock` and the like do not move
the version, so the numbers you are about to write are already correct.

### 0c. When you deferred the sync

Merge `main` down **after** the release commit, not before:

```bash
git merge origin/main --no-edit        # resolve conflicts here, in a real merge
grep -m1 '^version' pyproject.toml     # confirm the version did not move under you
make test && make frontend-test        # uv.lock may have moved; re-run before pushing
```

This is the repo's own pattern — see `Merge main back into development after the
vX.Y.Z release` in the history. Say in your Step 6 report that you took this route and
why, so the deviation is visible rather than silent.

### 0d. Whichever route you took

- **`sync-branches` stopped and asked something** → answer it (or relay it to the user)
  and let it finish. Do **not** start documenting on an unsynced tree.
- **The version moved during the sync** → re-read `pyproject.toml` in Step 1 rather
  than trusting anything you noted before it.
- **`testing` has commits `development` lacks** → check whether they are already in
  `origin/main` (`git branch -r --contains <sha>`). If they are, this is the ordinary
  release flow and nothing is wrong. If they are **not**, surface it to the user before
  documenting: something bypassed the release path and the docs would describe a tree
  nobody is going to ship.

Staging in this step is the one sanctioned exception to "never stage files yourself":
it is inherent to the stash-and-restore, and the user asked for that behaviour when they
invoked a skill that begins with it.

---

## Step 1 — Inspect what changed

Do not guess. Run these first and read the output:

```bash
git status --porcelain          # staged + unstaged
git diff --cached --stat        # summary of staged changes
git diff --cached               # full staged diff (use Grep on it if very large)
git log -10 --oneline           # recent commits for version/date context
git log HEAD --format='%s%n%b' -1   # full current commit message (if any)
grep '^version' pyproject.toml  # current version from pyproject
grep 'APP_VERSION' project/project/settings.py | head -1   # fallback default
git remote -v                   # owner/repo for CI badges

# Does this release drop schema? Exit 0 = clean, 1 = destructive, 2 = detector
# broke (never read that as clean). Run it ALWAYS, not only when the staged
# diff obviously touches migrations.
python scripts/detect_destructive_migrations.py --staged --format text

# Does this release add an import that is NOT at module top level? Same exit
# contract: 0 = clean, 1 = found, 2 = the detector broke (assume the worst).
# Run it ALWAYS. Reported in Step 6 so a human rules on each one BEFORE it
# ships — see the import rule in CLAUDE.md's "Code Organization".
python scripts/detect_non_top_level_imports.py --staged --format text
```

**If that reports destructive operations, you must surface it** — see Step 6.
This is the same script the release PR body, the testing-deploy email and both
production gates use, so what you report here is exactly what will block the
deploy later. v1.29.5 shipped five `RemoveField`s and nothing said so until a
human had already approved a production deploy the gate then refused.

If the working tree has **unstaged** changes that look relevant, ask the user whether to include them. Never stage files yourself without confirmation.

---

## Step 2 — Route staged files to the right docs

For every staged path, the table below tells you which docs are candidates for updates. A single staged change may touch many docs; apply the union.

| Staged path pattern | Docs to review |
|---------------------|----------------|
| `project/core/models.py`, `project/core/views/**`, `project/core/middleware.py`, `project/core/templates/**`, `project/core/static/**`, `project/core/decorators.py`, `project/core/context_processors.py` | `project/core/README.md` **+** main README (Architecture, Directory Layout, Features by View, Design Decisions) |
| `project/students/**/*.py` (models, forms, admin, urls) | `project/students/README.md` **+** main README (ER diagram if models changed, Directory Layout) |
| `project/billing/models.py`, `project/billing/services/**`, `project/billing/constants.py`, `project/billing/exports.py`, `project/billing/admin.py`, `project/billing/urls.py` | `project/billing/README.md` **+** main README (Database Schema, Features by View → Payments) **+** `project/ENROLLMENT_PAYMENT_SYSTEM.md` if pricing, discounts, or enrollment rules changed |
| `project/comms/services/**`, `project/comms/tasks.py`, `project/comms/management/commands/**`, `project/comms/urls.py` | `project/comms/README.md` **+** main README (Features by View → Apps) |
| `project/tests/**` | Main README (Testing section — test counts, per-file tables) **+** the app README whose logic the new test covers |
| `project/project/settings*.py` | Main README (Env Variables Reference, `.env template` code block) **+** `DEPLOYMENT.md` if a new env var |
| Any other Python file using `os.getenv(...)` | Main README (Env Variables Reference + `.env template` code block) **+** `DEPLOYMENT.md` Secret Manager list if the var carries a secret |
| `Makefile` | Main README (Make Commands table, Contributing → Make Commands Developer Tooling) **+** `CLAUDE.md` Gotchas if a command was renamed or removed |
| `pyproject.toml` | Main README (Tech Stack → Python Dependencies, Developer Tooling) **+** `docs/UV.md` if tooling-related **+** version badge and tables if version bumped |
| `uv.lock` | Usually no doc change needed (lock-file churn) — but confirm no version mismatch |
| `Dockerfile`, `docker-compose*.yml`, `entrypoint.sh` | Main README (Development & Docker, Testing Environment) **+** `DEPLOYMENT.md` if production image layout changed |
| `.github/workflows/**` | Main README (CI/CD section) **+** `docs/GITHUB.md` (full reference) |
| `.github/dependabot.yml`, `.github/CODEOWNERS` | Main README (CI/CD section) **+** `docs/GITHUB.md` |
| `docs/HTTPS.md` | Main README (Security → Transport, Testing Environment) |
| `docs/UV.md` | Main README (Tech Stack, Contributing) |
| `docs/CELERY.md` | Main README (Tech Stack), `project/comms/README.md` |
| `docs/GITHUB.md` | Main README CI/CD section (keep them in sync — README is the overview, GITHUB.md is the reference) |
| `DEPLOYMENT.md` | Main README (Project Status → Hosting column, GCP references) |
| `CLAUDE.md` | No downstream doc changes — but re-read it to confirm your edits don't contradict the rules |

---

## Step 3 — Per-file detailed checklists

### 3.1 — `README.md` (root)

Work through this list in order. **Do not skip a step just because nothing "looks" staged for it** — the last step greps for stale content and will catch anything the routing table missed.

#### a. Header badges (≈lines 11-18)

- Version badge must equal `pyproject.toml`'s version
- The CI badge must use the owner/repo from `git remote -v`. The coverage badge is a **static** shields.io badge (`img.shields.io/badge/coverage-<pct>%25-brightgreen`) in two places — the header row and the Project Status row — so its number must be updated by hand
- Don't bloat the header — no more badges than the originals + any new CI workflow

#### b. Project Status table — **three rows, exact order: Production → Testing → Development**

| Environment | Branch | Hosting | CI Status |
|-------------|--------|---------|-----------|
| **Production** | `main` | GCP Cloud Run + Cloud SQL (PostgreSQL 16), `europe-southwest1` | CI badge for `main` |
| **Testing (QA)** | `testing` | GCP Compute Engine e2-micro (free tier), Docker Compose | CI badge for `testing` |
| **Development** | `development` | Local machine via `make up` (Docker Compose) | CI badge for `development` |

If hosting changes (region, new managed service, etc.) update the Hosting cell. Never reorder rows. Never add rows.

#### c. Recent Versions table — **exactly 3 rows**

- Current version + the two previous patches
- When adding a new version, **delete the oldest row**
- Date in `YYYY-MM-DD`
- Description: **extremely brief — one short phrase, ≤10 words.** Name the headline change only, not every bullet. Good examples: "Favicon + social metadata, CI test-suite fixes" / "CI/CD pipeline + public-repo hardening" / "`update-readme` skill + docs overhaul". Bad examples (too long — move that detail to the Version History `<details>` block instead): anything with semicolons, parentheticals, or "plus …". The long-form per-version writeup lives in the Version History block, not here — this table is a glance-at-it index.

#### d. Version History `<details>` blocks

- Add a new `<details id="vXYZ" open>` at the top of the section
- **Remove** the `open` attribute from the previously-open block
- Content structure must match the existing blocks: `**Subsection**` bold headings + bullet lists. Typical subsections: GitHub Actions CI/CD, GCP migration, Dashboard enhancement, Developer tooling, Testing, Bug fixes, Public-repo hardening. Only include subsections that are actually relevant to what changed.
- Older blocks stay unless they contradict current state

#### e. Roadmap

If a roadmap item shipped, move its content to the Version History block for the shipping version and **remove** the roadmap entry. Do not leave completed items in the roadmap.

#### f. Tech Stack

- If `pyproject.toml` added/removed a package, update the Python Dependencies table
- If a new developer tool was adopted (e.g. detect-secrets, commitizen), add it to Developer Tooling

#### g. Database Schema

- If any `project/*/models.py` changed, compare fields to the Mermaid ER diagram and the Key Constraints table
- Model renames, new fields, deleted fields, new indexes, new UniqueConstraints — all must be reflected

#### h. Development & Docker

- `make help` command count changed? Update "60+ commands" (check with `grep -c '^[a-z].*:$' Makefile`)
- Quick Start commands still valid?
- Env Variables Reference: any new `os.getenv(...)` in `settings.py` → add a row
- (The App Versioning section was removed 2026-09-02 — do not re-add it; versioning is documented in CLAUDE.md)

#### i. Project Structure & Architecture → Directory Layout

Update every line that's wrong:

- `tests/` line: `pytest suite (N tests, X% coverage)` — get N the way the Testing section does (see k below: COLLECTED cases, not `def test_` lines) and X from the latest coverage report (`make test coverage`)
- `core/views/` annotation: view module count must be accurate
- `Makefile` line: command count
- New top-level files or directories (e.g. `.github/`, `docs/`, `scripts/`)
- Deleted files/directories (e.g. `render.yaml`)

Also refresh the App: core/students/billing/comms summary tables if models, view modules, or URL counts changed.

#### j. Features by View

- If a view was added or renamed, add/update the section
- If a feature was removed from a page, remove its bullet

#### k. Testing

- **The total test count is the number of tests pytest COLLECTS, not the number of `def test_` lines** (changed 2026-09-11). Get it from:
  `docker compose exec -T -e DJANGO_SETTINGS_MODULE=project.settings_test -e TEST_DB_HOST=db web python -m pytest project/tests/ --collect-only -q --no-cov -p no:randomly | tail -1`
  It reports what `make test` reports, which is the number a reader sees and can verify. A `def test_` grep undercounts every `@pytest.mark.parametrize` case — at the time of the switch that was 1,939 written functions against 2,226 collected cases, a gap of 287 across 61 parametrize decorators, which made the README look like tests had been LOST after a release that added them. Use the same metric for the two per-suite headers (`**N files, M tests.**`) and for every row of the per-file tables, so the rows SUM to the headline — that reconciliation is the check that the section is right, and it was impossible while the two metrics were mixed (96 of 106 rows were already collected counts and 10 were `def` counts).
- Per-file counts come from the same run:
  `... --collect-only -q --no-cov -p no:randomly | grep -E '^tests/(unit|integration)/.*\.py::' | awk -F'::' '{print $1}' | sort | uniq -c`
  Node IDs are relative to the container rootdir (`/app/project`), so they start `tests/`, not `project/tests/` — strip that prefix to match the README's link text.
- Coverage % must match the latest local coverage report (`make test coverage`; `make coverage-badge` renders an offline SVG)
- Per-file test tables: test counts per file must match
- **Coverage Report subsection** (see section k.1 below)

#### k.1 — Coverage Report subsection (end of Testing section)

At the very end of the Testing section (after the Integration Tests table, before the `---` separator that precedes the Migrations section), maintain a `### Coverage Report` subsection. It contains:

1. **A markdown table** listing every source file that is NOT at 100% coverage — i.e. the files that appear in pytest-cov's `term-missing` output (the 42 fully-covered files that are "skipped due to complete coverage" are omitted). Columns:

   | File | Stmts | Miss | Cover | Missing lines |
   | --- | --- | --- | --- | --- |

   To get the data, run `make test` inside Docker (or parse the most recent CI output) and copy the coverage table. Every row with `Cover < 100%` gets a row. Files at 100% are excluded (they're listed as "N files skipped due to complete coverage" — mention that count in the paragraph below).

2. **A brief analysis paragraph** immediately after the table. Format:

   ```
   **N files** have 100% coverage (skipped above). Total coverage: **X%** across Y statements.
   <health assessment sentence>
   ```

   The health assessment sentence uses these bands:

   | Coverage | Assessment |
   | --- | --- |
   | ≥ 95% | "Coverage is **very good**." |
   | 90–94% | "Coverage is **good**." |
   | 85–89% | "Coverage is **acceptable**." |
   | 80–84% | "Coverage is **bad** — it should be improved before the next release." |
   | 75–79% | "Coverage is **critical** — it needs immediate attention because pull requests to `main` are blocked below 75%." |
   | < 75% | "Coverage is **below the hard floor** — CI will fail and pre-commit will reject commits until coverage is restored above 75%." |

   After the assessment, add one sentence noting the enforcement: "Coverage is enforced at three levels: pre-commit hook (≥ 75%), CI hard floor (≥ 75%), and CI warning (< 90%)."

**Example** of the full subsection:

```markdown
### Coverage Report

| File | Stmts | Miss | Cover | Missing lines |
| --- | --- | --- | --- | --- |
| `billing/models.py` | 143 | 7 | 95% | 282-292, 361, 366 |
| `core/views/app_forms.py` | 615 | 46 | 93% | 51, 138-140, ... |
| ... | ... | ... | ... | ... |

**42 files** have 100% coverage (skipped above). Total coverage: **96%** across 2809 statements. Coverage is **very good**. Coverage is enforced at three levels: pre-commit hook (≥ 75%), CI hard floor (≥ 75%), and CI warning (< 90%).
```

**When updating:** replace the entire `### Coverage Report` subsection content (table + paragraph) with fresh data. Never leave stale coverage numbers — if tests changed, the coverage changed.

#### l. Security

- If a deployment platform was removed (e.g. Render), delete every row, subsection, and mention. **Always** run `grep -in 'render\|gcp-cloudrun'` on the entire README before claiming it's clean.
- If a security protection is now enabled (e.g. GitHub secret scanning, push protection), **move** its row from "Future Security Improvements" to the relevant active table.
- If `SECURE_*` settings changed, update the Transport Security and Security Headers tables.

#### m. Testing Environment (QA)

- QA access instructions still accurate?
- Any env var renames in `.env.testing`?
- The GCP deployment plan is **gone** from this section — it lives only in `DEPLOYMENT.md` now. If it reappears, delete it.

#### n. CI/CD & GitHub Actions

- Workflows table: one row per `.github/workflows/*.yml` file
- Pipeline Overview diagram: update if flow steps changed
- Branch Protection tables: match what's actually configured (or what the user wants configured)
- Public Repository Hardening: a new protection toggle → a new row
- Required GitHub Secrets: every secret referenced in any workflow `${{ secrets.X }}` must appear in this table. Grep: `grep -rh 'secrets\.' .github/workflows/ | sort -u`.
- Email Notifications: one row per notification email sent by any workflow

#### o. Contributing

- Development Workflow numbered list: current `make` commands, current commit message convention
- Make Commands (Developer Tooling) table: every tool in `pyproject.toml` dev-dependencies should have a row with its `make` command

#### p. Table of Contents

Every `##` and `###` and `####` heading in the body must have a corresponding ToC entry. GitHub anchor rules:

- Lowercase
- Replace spaces with `-`
- Drop everything except `a-z 0-9 - _`
- Escape `&` as `\&` (escaped only in the ToC link text, not in the anchor itself)

Deleted headings → delete the ToC entry. New headings → add an entry at the correct nesting depth.

#### q. Stale-reference grep (MUST run before finishing)

For every file or feature removed this session, grep the entire README. Examples:

```bash
grep -n "render\|render\.yaml\|gcp-cloudrun" README.md    # Render removed
grep -n "pre-commit-run" README.md                        # renamed to pc-run
grep -n "174 tests\|132 tests\|252 tests" README.md       # old test counts
grep -n "V=1" README.md                                    # old `make version V=x.y.z` syntax
grep -n "webcrumbs" README.md                              # CSS wrapper removed
```

Any hit → fix it.

---

### 3.2 — `CLAUDE.md`

CLAUDE.md is the rulebook for future AI agents. Check if the staged diff introduces:

- A **new convention** (e.g. "always use `transaction.atomic()` for multi-model writes") — add to "Django Best Practices" or the relevant section
- A **new gotcha** (something non-obvious that tripped someone) — add to "Gotchas"
- A **renamed command, file, or symbol** — update the reference (e.g. `pre-commit-run` → `pc-run`, `make version V=x` → `make version x`)
- A **new tool or service** — add to "How to run" or "Developer tooling"
- A **new app** (unlikely) — add to "4 Django apps"

The README-maintenance checklist inside CLAUDE.md (section "README maintenance") must stay in sync with the detailed Step 3.1 above. If you add/remove a step in the skill, update CLAUDE.md too.

---

### 3.3 — `DEPLOYMENT.md`

Check when the staged diff touches infrastructure, Docker, settings, secrets, or anything that would change a production deploy.

- **Architecture diagram** — services still accurate?
- **Environments at a Glance** table — Development / Testing / Production all still correct?
- **Prerequisites** — any new required tool?
- **Initial Setup commands** — new service to enable, new secret to create?
- **Build & Deploy** — env-var list on the `gcloud run deploy` command must match every `os.getenv(...)` in `settings.py`. Missing env vars cause production bugs.
- **Celery strategy** — if Celery tasks were added/removed/renamed, update the Cloud Scheduler + Cloud Run Jobs sections
- **Custom domain** — only update if the domain mapping command changed
- **Cost estimates** — if hosting assumptions changed (e.g. `db-f1-micro` → `db-custom-1-3840`), recalculate
- **Optional Services for Future Evolution** — move an entry out of this section if it's now implemented

---

### 3.4 — `docs/GITHUB.md`

This is the CI/CD reference. README has the overview; GITHUB.md has the full walkthrough. They must not contradict each other.

- **Pipeline Overview diagram** — matches README's
- **Every workflow** has its own subsection with: what it does, when it triggers, jobs, any quirks
- **Branch Protection** subsections (main, testing): every rule and required status check listed
- **Public Repository Hardening** — table matches README's
- **Required GitHub Secrets** — every `${{ secrets.X }}` in any workflow appears here with: required-by workflow, purpose, creation instructions where non-obvious (e.g. `GH_PAT` fine-grained token scopes)
- **Email Notifications** table — matches README's
- **Dependabot + CodeQL** sections — accurate

---

### 3.5 — `docs/HTTPS.md`, `docs/UV.md`, `docs/CELERY.md`

Update these only when the corresponding tool or process changed. These are narrow, focused docs.

- `HTTPS.md`: if the Nginx config or the Cloud Run TLS setup changed
- `UV.md`: if UV workflow commands, lock behaviour, or dependency groups changed
- `CELERY.md`: if worker/beat setup, task queues, or Redis configuration changed

---

### 3.6 — `docs/TODO.md`

If the staged work **completes** a TODO item, remove it from this file. If it creates new follow-up work, add an entry with context.

---

### 3.7 — Per-app READMEs (`project/<app>/README.md`)

Each app README has the same structure. When the app's source changes, keep these in sync:

| Section | Source of truth | How to verify |
|---------|----------------|---------------|
| **Models table** | `project/<app>/models.py` | Every model class must have a row with table name and purpose |
| **Views table** (core only) | `project/core/views/*.py` | One row per view module, listing every view function |
| **Service Layer** (billing only) | `project/billing/services/*.py` | Every service class + every public method must be documented |
| **Forms** | `project/<app>/forms.py` | Every ModelForm/Form subclass listed |
| **Admin** | `project/<app>/admin.py` | Every `register`ed model with custom admin behaviour |
| **URLs** | `project/<app>/urls.py` | URL pattern count ("12 URL patterns"); mention each route group |
| **Management commands** | `project/<app>/management/commands/*.py` | Every file listed with a one-line description |
| **Celery tasks** (comms only) | `project/comms/tasks.py` | Every `@shared_task` listed with a one-line description |

If a new model/view/service/form/command/task was added in the staged diff, its app README must mention it.

---

### 3.8 — `project/ENROLLMENT_PAYMENT_SYSTEM.md`

This is the **authoritative spec** for pricing and billing rules. If the staged diff touches:

- `project/billing/constants.py` (pricing seeds)
- `project/billing/services/pricing_service.py`, `enrollment_service.py`, or `payment_service.py`
- `SiteConfiguration` model fields
- The enrollment flow or discount logic

then this file must be re-checked. The prices, discounts, and rules here must match the code.

---

### 3.9 — The `.env template` code block in `README.md`

`.env.example` **does not exist in this repo**. Instead, the README's Development & Docker section contains a `.env template` heading followed by a fenced `bash` code block that documents every variable the app reads. Contributors copy this block into a new `.env` file and fill in the blanks.

This block is the authoritative env-var spec for local dev. The skill keeps it healthy.

**Required checks:**

1. **Code block exists and is findable** — the README must contain a heading literally `### .env template` followed by a fenced \`\`\`bash block. Verify with:

   ```bash
   grep -n "^### \.env template" README.md
   ```

   If missing, flag it immediately — the Quick Start links to `#env-template` and `make setup` tells users to find this block.

2. **Every var has an empty or safe value — no real secrets.** When the user asks you to refresh this block from `.env`, you may read `.env` to extract variable names and section-comment structure, but:

   - Every value slot must be **empty** (`KEY=`) **or a safe default** that's already public information.
   - **Safe defaults** (OK to ship): `localhost`, `127.0.0.1`, `0.0.0.0`, `db`, `redis://redis:6379/0`, `fiveaday_db`, `fiveaday_user`, `5432`, `8000`, `True`/`False`, `development`/`production`/`testing`, `INFO`/`DEBUG`, `postgres`, `fiveaday` (the well-known legacy default username), `http://localhost:8000/auth/google/callback/`.
   - **Never acceptable** — anything that looks like a real secret and must be stripped before writing:
     - Random 50-char Django secret keys (anything with high-entropy symbols)
     - `ghp_` / `ghs_` / `gho_` / `github_pat_` prefixes (GitHub tokens)
     - `GOCSPX-` prefix (Google OAuth client secrets)
     - 72-char base64 strings (Django's `get_random_secret_key()` output, `openssl rand -base64` output)
     - 16-char lowercase-letter groups like `krqg zqeq kcxc onub` (Gmail App Passwords)
     - Real email addresses of the maintainer or users (the maintainer knows theirs — the README should say `your-academy@gmail.com` or leave blank)
     - Real IBANs, phone numbers, business details
     - Real Google OAuth client IDs (start with digits then `-...-apps.googleusercontent.com`)

   If you see any of these when reading `.env`, **strip them** before writing to the README. Flag to the user that their `.env` contained secrets you saw.

3. **Coverage matches the code** — every env var the app actually reads must appear in the template. Compare:

   ```bash
   # Every env var referenced in Python code (most authoritative)
   grep -rhoE 'os\.getenv\("[A-Z_]+"' project/ | sort -u | sed 's/os\.getenv("//'

   # Every var in the README template
   grep -E '^[A-Z_]+=' README.md | cut -d= -f1 | sort -u
   ```

   The set difference tells you what's missing (code but not template) or stale (template but no longer read). Both sides should be addressed — missing → add a line under the right section, stale → remove from the template.

4. **Coverage matches the Env Variables Reference table** — every row in the README's **Environment Variables Reference** table (the `| Variable | Description |...` table) should also appear in the template code block, unless it is explicitly marked "advanced override" (vars like `SESSION_COOKIE_AGE`, `APP_VERSION`, `GOOGLE_ALLOWED_EMAIL` can be left out of the template as long as they're in the reference table).

5. **No duplicate keys** in the template — inside the fenced block:

   ```bash
   awk '/^### \.env template/{flag=1; next} /^```bash/{capture=flag; next} /^```/{capture=0; flag=0} capture && /^[A-Z_]+=/ {sub(/=.*/,""); print}' README.md | sort | uniq -d
   ```

   must be empty.

6. **Grouping and comments preserved** — the template uses `# ====...====` banner dividers before each section (DJANGO SETTINGS, LOGGING, DATABASE CONFIGURATION, SUPERUSER, EMAIL CONFIGURATION, CELERY / REDIS, AUTHENTICATION, GOOGLE OAUTH, ACADEMY BUSINESS INFO). A new var must be placed in the right section — never appended at the bottom.

7. **`.gitignore` is correct** — should have `.env*` with **no** exceptions (no `!.env.example` line). Verify:

   ```bash
   grep -n '\.env' .gitignore
   ```

   If `!.env.example` or `!.env.testing.example` or any other `!.env*` exception exists, remove it.

8. **No stale `.env.example` references anywhere** — grep the full docs tree and Makefile:

   ```bash
   grep -rn '\.env\.example\|\.env\.testing\.example' README.md CLAUDE.md DEPLOYMENT.md docs/ Makefile project/
   ```

   Any hit → remove or replace with a pointer to the README template.

9. **Legacy / deprecated vars** — if `.env` contains a var that the app no longer reads (e.g. `VERSION=0.30.2` from an old release), do not add it to the template. Flag it to the user so they can clean their real `.env`.

10. **ToC entry** — the ToC must include `- [.env template](#env-template)` under Development & Docker.

---

## Step 4 — Cross-file consistency sweep

Certain facts appear in multiple documents. When they change, update **every** occurrence:

| Fact | Files that reference it |
|------|-------------------------|
| **Current version (`x.y.z`)** | Main README header badge, Recent Versions top row, Version History latest `<details>`, `pyproject.toml`, `project/project/settings.py` APP_VERSION default |
| **Test count (`N tests`)** | Main README header (Project Status area if mentioned), Testing section top, Directory Layout `tests/` annotation, per-app READMEs if they cite a count |
| **Coverage %** | Main README coverage badge (static, **both** badge rows), Testing Overview table, Directory Layout |
| **Make command count** | Main README Directory Layout (`60+ commands`), help text |
| **Python version (`3.12+`)** | README badge, `pyproject.toml` `requires-python`, Dockerfile `FROM python:3.12-slim`, `[tool.ruff] target-version = "py312"` |
| **Django version (`5.2`)** | README badge, `pyproject.toml` dependency |
| **PostgreSQL version (`16`)** | README badge, `docker-compose.yml`, `DEPLOYMENT.md` Cloud SQL create command, CI workflow service image |
| **GCP region (`europe-southwest1`)** | Project Status table, DEPLOYMENT.md (every `gcloud` example) |
| **Owner/repo (`starseeker-code-public/five-a-day`)** | README badges (CI), GITHUB.md examples |
| **Env var inventory** | `settings.py` (`os.getenv(...)`), README Env Variables Reference table, `.env.example`, DEPLOYMENT.md Secret Manager list. Every name that appears in one must appear in every other (modulo internal-only vars that never ship to production). |

If a change appears in one place, **check the others**. A version-bump commit that only updates the badge but not Version History is a broken commit.

---

## Step 5 — Global stale-reference sweep

Before reporting completion, grep the entire `docs/` tree, main README, all per-app READMEs, CLAUDE.md, and DEPLOYMENT.md for:

- File names of any file deleted this session (e.g. `render.yaml`, old workflow files)
- Old command names (if renamed)
- Old env var names (if renamed)
- Legacy service names (e.g. "Render", "Heroku") unless they are historical in Version History
- Deprecated URL patterns
- Removed models

Commands:

```bash
grep -rn "<removed-thing>" README.md CLAUDE.md DEPLOYMENT.md docs/ project/*/README.md project/ENROLLMENT_PAYMENT_SYSTEM.md
```

Any hit outside `.venv/` → fix.

---

## Step 6 — Report back

After saving all docs, report in under 25 lines.

**If Step 1's destructive-migration scan exited 1, the FIRST thing in the report
is a warning block** — above the file list, not buried in it. The developer is
about to ship this, and the cost of them learning it later is a wasted
production approval (the deploy arms, a human clicks Approve, and Gate 3 or the
deploy gate refuses). State:

- which migration files drop or rename schema, with the operations found
- that the production deploy must be started from *Actions → Deploy production →
  Run workflow* with **`ack_destructive`** ticked — and **not** `force`, which
  also skips the QA sign-off, the provenance gate and the version compare
- that migrations run BEFORE the service rolls, so the **currently deployed**
  code runs against the new schema for the whole rollout window. Ask explicitly
  whether that old code still reads what is being removed: if it does, the
  window is an outage and the change should be split (ship the code that stops
  using the column first, drop it in the next release)

Then the usual report:

- **Files updated** — list each file with a one-sentence summary of what changed
- **Versions moved** — which versions entered/exited the Recent Versions table
- **Inconsistencies resolved** — e.g. "Main README badge said 1.0.4, pyproject said 1.0.5 — aligned to 1.0.5"
- **Cross-file syncs** — e.g. "Updated test count in main README, core/README.md, and CLAUDE.md"
- **Stale references removed** — e.g. "Removed 4 remaining `render.yaml` mentions in Security and ToC"
- **Anything you noticed but did NOT change** — sections that look stale but weren't in scope (surface these for the user to decide)
- **Files NOT touched and why** — if only `docs/CELERY.md` was in scope for the staged diff, say so explicitly
- **A commit messagge with a super brief description of all changes** - e.g. "Created inspirational quote generator, cleaned legacy render config files and prepared everything for googl e cloud migration and updated versioning and precommit make commands"

**Finally, if Step 1's non-top-level-import scan exited 1, close the report with
a `Non-top-level imports` section** — LAST, after the commit message, because it
is a question for the developer rather than a summary of work done. This is a
judgement call the tooling deliberately does not make: list every hit and, for
each, write **one sentence saying whether you think it is justified and why**, so
the developer can rule on it. Do not quietly accept them, and do not "fix" them
here — the release is already staged.

Report each as `file:line` + the import + the shape the detector matched
(`import-guard`, `type-checking`, `sole-statement`, `deferred`) + your verdict.
The shape is a hint, not an answer: `deferred` is the category that is nearly
always an accident, but a `try/except ImportError` around a package that IS a
declared dependency is just as wrong. The reasons that genuinely justify one are
listed in CLAUDE.md's import rule — a real import cycle, `AppConfig.ready()`
app-registry timing, an optional or undeclared dependency, the import being the
conditional itself, `TYPE_CHECKING`, or a MEASURED import cost. If a hit matches
none of those, say so plainly and recommend hoisting it.

Two things to flag explicitly when you see them, because both are silent:

- **an existing hit with no comment explaining why it is deferred** — the rule is
  that the reason lives at the import, so a bare one is indistinguishable from an
  accident and will be "fixed" by someone eventually
- **a new deferred import whose symbol is `mock.patch`ed anywhere in the tree** —
  grep for it. Hoisting it later breaks that patch, and hoisting it *now* means
  the patch target must move to the using module in the same change

If the scan exited 0, say so in one line — it is a positive result worth stating.

---

## Guarantees

- **Never commit.** The user may want to amend, combine, or review before committing.
- **Never stage files** yourself without user confirmation — *except* in Step 0 **when
  you take the `sync-branches` route**, where that skill stages everything in order to
  stash it and restores it staged afterwards. That is the whole point of the step, not
  an incidental side effect. On the deferred-merge route (0c) nothing is stashed, so the
  ordinary rule applies and the index stays exactly as the user left it.
- **Never read `.env*` files** — they contain secrets.
- **Never invent work** — if the staged diff is purely a bug fix with no documentation implications, say "no doc changes needed" and stop.
- **Never fabricate counts** — run the grep/wc command to get the real test count, view count, etc.
- **Flag version mismatches** between `pyproject.toml`, `settings.py`, and the most recent commit message — the user likely forgot `make version`.
- **Always grep** for stale references before declaring victory. The routing table misses things; grep catches them.

## When the staged diff is empty

If `git diff --cached` is empty, either:
1. The user forgot to stage anything → ask
2. They want you to review the working tree instead → ask for confirmation and then `git diff` (unstaged) is the source of truth

Do not guess which case it is.

## When the staged diff spans multiple versions

If the staged changes represent more than one logical version (e.g. a CI/CD refactor + a new feature + a bug fix), flag this to the user: they probably want to split the commit. Do not try to document them as a single version.
