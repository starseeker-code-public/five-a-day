---
name: analyze
description: Use when the user wants a comprehensive review of the whole codebase (not a diff) — a measured census (total lines of code broken down by language and category), a structural inventory, then per-area deep dives across architecture, database, backend, frontend, security, testing, DevOps & CI/CD, infrastructure, code quality and performance — each opening with a plain-language sentence for non-technical readers. Produces graded sections backed by evidence and a short prioritised action list. Triggers on "review the whole project", "full code review", "analyse the codebase", "give me a general review", "review each section", "/analyze".
---

# analyze

A whole-codebase review. **Not** a diff review — `/code-review` does that. This one
answers "what is the state of this project?" across every layer, and it is judged on
whether the user can act on it.

## Scope: the ENTIRE project

**Every tracked file, and the full commit history.** The review is *independent of the
current git state* — staged work, a dirty tree, or a release in flight change nothing
about what gets examined.

Specifically, do **not**:

- scope anything to `git diff`, `git diff --cached`, or a commit range;
- window the history (`--since=…`) when measuring churn — use all of it;
- probe one app or one directory when the pattern applies tree-wide. Running the
  swallowed-exception scan over `core/views/` alone found 15 hits; over the whole tree it
  found 24, and the extra 9 included the ones in `context_processors.py` that execute on
  **every page**;
- treat non-Python as out of scope. Templates, JavaScript, CSS, workflow YAML, the
  Dockerfile, the Makefile and shell scripts are all part of the project — on the
  reference project the largest single file after the top four was a **703-line GitHub
  Actions workflow**.

Naming the version or git state as one line of context is fine. Framing the report as a
review "at staged vX.Y" is not: it reads as a diff review and invites the reader to
assume unchanged code went unexamined.

**Part B.0 makes completeness measurable. Do not skip it** — it is the difference between
reviewing the project and reviewing the parts that happened to come to mind.

The report has three parts: a **measured census**, **graded sections** per area, and
**three things to do next**. The census is what makes the rest credible — every claim in
the sections should trace back to something counted or proven.

**All commands below are tested and produce real output.** Run them; do not paraphrase
their results from memory.

---

## Part A — The rules that make this worth reading

### 1. Never estimate a number. Run the command.

Every count in the report — lines of code, models, indexes, routes, `except` blocks,
functions over 100 lines, accessibility attributes — comes from a command whose output
you saw. If you cannot measure it, do not state it.

A reader who catches one fabricated number correctly stops trusting the whole document.

### 2. Go and read the parts you have not read.

The census (Part B) tells you where the mass is. Open the biggest files and the modules
you have never looked at. The interesting findings are almost always in the file nobody
reviews because it is 1,300 lines long.

If you skip an area, **say so explicitly in the report** rather than writing a
confident-sounding paragraph from inference.

### 3. Prove a defect before you claim it.

Reasoning about library behaviour is how you produce a wrong finding. Run it.

Real examples from the review this skill came from — each would otherwise have been a
guess, and one would have been wrong:

| Claim | How it was proven |
|---|---|
| openpyxl turns a leading `=` into a live formula | `ws.append([...])`, printed `cell.data_type` → `'f'` |
| reportlab does not parse markup in a plain Table cell | read `Table._drawCell` → `canv.drawString` on the string branch |
| `Decimal("NaN") <= 0` raises | ran it → `InvalidOperation` |
| an out-of-range `__year` lookup 500s | ran the ORM query → `OverflowError` / `ValueError` |
| `DatabaseCache.incr` is not atomic | `'incr' in DatabaseCache.__dict__` → False, then read `BaseCache.incr` → get-then-set |

For anything you could not prove, label it "unverified" and say what proof would take.

### 4. Distrust a harness that passes.

The most valuable lesson available here. A sweep of every admin view once reported zero
problems — because middleware was redirecting every request to `/login/` and the check
only flagged `status >= 400`. It was walking redirects, not pages.

Before believing a green result, ask **what would this have looked like if it were
broken?** If the answer is "the same", the harness is wrong. Concretely:

- Assert on a **success** signal (200 *and* expected content), not the absence of an error.
- Treat a redirect on a GET as a failure unless you meant it.
- Check fixtures actually establish the state you assume — a missing secret makes every
  signature check return False and every assertion pass.

### 5. Separate real hits from noise.

Greps over a mature repo produce heavy false positives, and so do the analysis scripts
below. Every hit gets checked before it becomes a finding. Usual culprits:

- **Historical records** — a removed thing named in a changelog or Version History entry
  is not stale.
- **Prose in backticks** — a literal `` `<details>` `` in a sentence broke a tag-balance
  count until non-line-start matches were excluded.
- **Near-miss identifiers** — `_oauth_log.exception` does not match a grep for
  `logger.exception`; `payment_detail` and `payment_detail_view` are different functions.
- **Framework registration** — a `ModelAdmin`, `AppConfig` or pytest fixture is never
  *referenced* by name, so naive dead-code detection flags all of them. The dead-code
  script below went from 39 hits to 6 real candidates once these were excluded.

Report the count of **real** findings, not of raw hits.

### 6. Grade honestly, and name the dominant weakness.

A review where everything is "good" is worthless. Find the *pattern* behind the
individual findings and state it — that is the part a linter cannot give them.

The review this came from found that every bug was a **consistency** failure, not a
correctness one: someone knew the rule and had written it correctly somewhere else. That
one sentence was worth more than the list of bugs.

### 7. Credit what is genuinely good, specifically.

Not "good test coverage" — "0.93:1 test-to-app LOC ratio, run against the same
PostgreSQL as production, with a hard 75% floor enforced at three levels". Specific
praise tells the user which habits to keep.

---

## Part B — Census (run this first)

### B.0 Completeness ledger — what must be read

Run this **before** anything else. It enumerates every reviewable source file and buckets
it by size, so "the entire project" becomes a checklist rather than an intention.

```python
import subprocess
SRC = (".py", ".html", ".js", ".ts", ".tsx", ".css", ".scss", ".sh", ".yml", ".yaml", ".toml")
files = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split("\n")
rows = []
for f in files:
    if not f or not f.endswith(SRC) or "/migrations/" in f:
        continue
    try: n = sum(1 for _ in open(f, encoding="utf-8", errors="ignore"))
    except OSError: continue
    rows.append((n, f, "test" if ("/tests/" in f or f.endswith("conftest.py")) else "source"))
rows.sort(reverse=True)
src = [r for r in rows if r[2] == "source"]
tot = sum(r[0] for r in src)
print(f"REVIEWABLE SOURCE: {len(src)} files, {tot} lines (tests & migrations excluded)\n")
for label, lo, hi in [("> 500 lines", 500, None), ("200-500", 200, 500),
                      ("50-199", 50, 200), ("< 50", 0, 50)]:
    sel = [r for r in src if r[0] > lo and (hi is None or r[0] <= hi)]
    print(f"  {label:12} {len(sel):4d} files  {sum(r[0] for r in sel):6d} lines "
          f"({100*sum(r[0] for r in sel)/tot:4.1f}%)")
print("\nMUST be read in full (> 500 lines):")
for n, f, _ in [r for r in src if r[0] > 500]:
    print(f"  {n:5d}  {f}")
```

**The rules this imposes:**

- **Every file over 500 lines is read in full.** No exceptions. On the reference project
  that was 13 files carrying 23.9% of all source — including a 703-line workflow, a
  526-line CSS file and two ~550-line templates that a Python-focused review would never
  have opened. A first pass of this skill read only 7 of the 13 and the omission was
  invisible until this ledger existed.
- **Every file in the 200–500 bucket is at least skimmed** for structure and obvious
  smells. That bucket is usually the largest share of source (45.4% on the reference
  project) — skipping it means most of the codebase went unseen.
- **Below 200 lines**, structural probes (the greps in Part C) are sufficient.

Carry the numbers into the report's **Coverage** section (Part D) and state honestly how
many of each bucket you actually read. A measured admission is useful; silence is not.

### B.1 Total lines of code, by type and by category

Writes a TSV then reports two tables. **Tested and working**; adjust the `case` arms for
the project's languages.

```bash
git ls-files | while read -r f; do
  n=$(wc -l < "$f" 2>/dev/null || echo 0)
  case "$f" in
    */tests/*|*/conftest.py|conftest.py)          k="python (tests)";      c=tests ;;
    */migrations/*.py)                            k="python (migrations)"; c=code ;;
    *.py)                                         k="python (app)";        c=code ;;
    *.html)                                       k="html templates";      c=code ;;
    *.js)                                         k="javascript";          c=code ;;
    *.ts|*.tsx|*.jsx)                             k="typescript";          c=code ;;
    *.css|*.scss)                                 k="css";                 c=code ;;
    *.md)                                         k="markdown docs";       c=docs ;;
    *.yml|*.yaml)                                 k="yaml (ci/compose)";   c=config ;;
    *.toml|*.cfg|*.ini)                           k="toml/ini";            c=config ;;
    *.sh)                                         k="shell scripts";       c=config ;;
    Makefile|*/Makefile)                          k="Makefile";            c=config ;;
    Dockerfile*|*/Dockerfile*)                    k="Dockerfile";          c=config ;;
    *.lock)                                       k="lockfile";            c=generated ;;
    *.png|*.jpg|*.jpeg|*.ico|*.svg|*.webmanifest) k="static assets";       c=assets ;;
    *) k="other"; c=other ;;
  esac
  printf "%s\t%s\t%s\n" "$k" "$c" "$n"
done > /tmp/_census.tsv

TOTAL=$(awk -F'\t' '{s+=$3} END {print s}' /tmp/_census.tsv)
echo "TOTAL TRACKED LINES: $TOTAL across $(git ls-files | wc -l) files"; echo
printf "  %-22s %8s %7s %7s\n" "TYPE" "LINES" "FILES" "SHARE"
awk -F'\t' -v T="$TOTAL" '{L[$1]+=$3; F[$1]++} END {for (k in L) printf "%d\t%d\t%.1f\t%s\n", L[k], F[k], 100*L[k]/T, k}' /tmp/_census.tsv \
  | sort -rn | awk -F'\t' '{printf "  %-22s %8d %7d %6.1f%%\n", $4, $1, $2, $3}'
echo
printf "  %-22s %8s %7s\n" "CATEGORY" "LINES" "SHARE"
awk -F'\t' -v T="$TOTAL" '{C[$2]+=$3} END {for (k in C) printf "%d\t%.1f\t%s\n", C[k], 100*C[k]/T, k}' /tmp/_census.tsv \
  | sort -rn | awk -F'\t' '{printf "  %-22s %8d %6.1f%%\n", $3, $1, $2}'
```

**Sorting gotcha:** never `sort -k2 -rn` on the formatted table — multi-word type names
("python (tests)") shift the columns and the sort silently uses the wrong field. Emit the
number first, sort, then reformat. That bug was live in the first version of this skill.

**How to read it.** The category rollup is the headline. Useful ratios:

- **tests ÷ code** — under ~0.3 is thin; around 0.5–1.0 is healthy; over ~1.5 may mean
  brittle or duplicated tests.
- **docs share** — over ~10% is unusual and usually a strength worth naming.
- **config share** — over ~15% in a small project suggests infrastructure sprawl.
- **generated** (lockfiles) should be small; if it dominates, exclude it and say so.

### B.2 Largest files and complexity distribution

```bash
git ls-files 'project/*.py' | grep -v '/tests/' | grep -v migrations | xargs wc -l | sort -rn | head -15
```

Then function length, which is a better complexity proxy than file length:

```python
import ast, glob
rows = []
for path in glob.glob("project/**/*.py", recursive=True):
    if "/tests/" in path.replace("\\","/") or "migrations" in path:
        continue
    try: tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception: continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.end_lineno:
            rows.append((node.end_lineno - node.lineno + 1,
                         path.replace("project/","").replace("\\","/"), node.name))
rows.sort(reverse=True)
for n, p, fn in rows[:12]:
    print(f"  {n:4d} lines  {p}::{fn}")
lens = sorted(r[0] for r in rows)
print(f"  --- {len(rows)} functions, median {lens[len(lens)//2]}, "
      f"p90 {lens[int(len(lens)*0.9)]}, over-100 {sum(1 for x in lens if x > 100)}")
```

A healthy median (10–20 lines) alongside a long tail is the common shape. **Report both**
— "median 13 lines, but 10 functions over 118 and 5 of them in one file" is far more
actionable than either number alone.

### B.3 Structural inventory

```bash
# apps, models, tables, indexes, constraints
grep -c '^class .*models.Model' project/*/models.py
grep -c 'db_table' project/*/models.py
grep -c 'models.Index' project/*/models.py
grep -c 'Constraint'  project/*/models.py
grep -rhoE "on_delete=models\.[A-Z_]+" project/*/models.py | sort | uniq -c
grep -rc "FloatField" project/*/models.py            # money safety: want zero

# routes per app
for f in project/*/urls.py project/project/urls.py; do
  printf "  %-30s %3d routes\n" "$f" "$(grep -cE '^\s*path\(' "$f")"; done

# the rest of the surface
ls project/*/migrations/*.py | grep -v __init__ | wc -l      # migrations
ls project/*/management/commands/*.py | grep -v __init__ | wc -l
grep -c "@shared_task" project/*/tasks.py                     # celery tasks
grep -rc "@admin.register\|admin.site.register" project/*/admin.py
grep -c "@pytest.fixture" project/conftest.py
ls .github/workflows/ | wc -l
git ls-files '*.html' | wc -l                                 # templates
git ls-files '*.js' | wc -l                                   # js modules
```

### B.4 Documentation density

```python
import glob, re
code = com = doc = 0
for p in glob.glob("project/**/*.py", recursive=True):
    if "/tests/" in p.replace("\\","/") or "migrations" in p: continue
    src = open(p, encoding="utf-8").read(); lines = src.split("\n")
    code += len([l for l in lines if l.strip()])
    com  += len([l for l in lines if l.strip().startswith("#")])
    doc  += sum(len(m.split("\n")) for m in re.findall(r'"""[\s\S]*?"""', src))
print(f"  non-blank {code}, comments {com} ({100*com/code:.1f}%), "
      f"docstrings {doc} ({100*doc/code:.1f}%), documented {100*(com+doc)/code:.1f}%")
```

Above ~20% documented is a genuine strength; name it. Below ~5% in a codebase with
non-obvious business rules is a real risk and belongs in the concerns.

### B.5 Churn — where the risk actually lives

Use the **full history**, not a window — a six-month window on a year-old project hides
half the signal. Filter to files that still exist, or deleted files pollute the ranking
(`core/views.py` ranked 5th on the reference project with 48 changes; it was split into a
package and no longer exists).

```bash
printf "commits: %s | first: %s | last: %s\n" "$(git rev-list --count HEAD)" \
  "$(git log --reverse --format=%ad --date=short | head -1)" \
  "$(git log -1 --format=%ad --date=short)"

git log --format=format: --name-only | grep -vE '^$' | sort | uniq -c | sort -rn | head -30 \
  | while read -r n f; do [ -f "$f" ] && printf "  %5d  %s\n" "$n" "$f"; done | head -12
```

High churn plus high complexity is the classic hotspot. A settings file changed 87 times
across 233 commits is telling you where the operational pain is.

---

## Part C — Per-area probes

### Database

```bash
grep -rn "transaction.atomic" --include="*.py" project/ | grep -v tests | wc -l
grep -rn "select_related\|prefetch_related" --include="*.py" project/ | grep -v tests | wc -l
```

Check: explicit `db_table` everywhere, `Decimal` (never `Float`) for money, indexes on
the fields actually filtered, `UniqueConstraint` for real invariants, and the `on_delete`
mix — heavy `CASCADE` on financial records is a red flag; `PROTECT`-dominant is a good
sign.

### Backend

```bash
grep -rn "full_clean(" --include="*.py" project/ | grep -v tests       # validated writes
grep -rn "objects.create(" --include="*.py" project/*/views/ | wc -l   # unvalidated writes
```

Then **silently swallowed exceptions** — consistently one of the highest-value findings,
because it is where production failures go to die:

```python
import glob, re
for path in glob.glob("project/**/*.py", recursive=True):      # WHOLE TREE, not one app
    q = path.replace("\\", "/")
    if "/tests/" in q or "migrations" in q:
        continue
    lines = open(path, encoding="utf-8").read().split("\n")
    for i, ln in enumerate(lines):
        if re.match(r"\s*except Exception", ln):
            window = "\n".join(lines[i:i+6])
            if not re.search(r"log(ger)?[a-z_]*\.(exception|error|warning)", window):
                print(f"{path}:{i+1}  -> {lines[i+1].strip()[:60]}")
```

Triage each: `pass  # never fail the request over email` is a documented decision;
`error_count += 1` in a bulk-send loop means an operator sees "3 failed" and can never
find out why.

### Dead code

Framework-registered names are never referenced, so exclude them or the output is
useless (39 hits vs 6 real candidates on the reference project):

```python
import ast, glob, re
SKIP_FILES = ("admin.py","apps.py","conftest.py","settings.py","urls.py","wsgi.py","asgi.py","celery.py")
SKIP_BASES = ("Migration","AppConfig","ModelAdmin","Meta")
srcs = {p: open(p, encoding="utf-8").read()
        for p in glob.glob("project/**/*.py", recursive=True) if "migrations" not in p}
app = {p: s for p, s in srcs.items() if "/tests/" not in p.replace("\\","/")}
tests_blob = "\n".join(s for p, s in srcs.items() if "/tests/" in p.replace("\\","/"))
for p, s in app.items():
    if p.endswith(SKIP_FILES): continue
    try: tree = ast.parse(s)
    except Exception: continue
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.ClassDef)) or node.name.startswith("_"):
            continue
        if isinstance(node, ast.ClassDef) and any(
            getattr(b, "attr", getattr(b, "id", "")) in SKIP_BASES for b in node.bases):
            continue
        n = node.name
        if sum(len(re.findall(rf"\b{re.escape(n)}\b", o)) for q, o in app.items() if q != p) == 0 \
           and len(re.findall(rf"\b{re.escape(n)}\b", s)) - 1 == 0:
            t = len(re.findall(rf"\b{re.escape(n)}\b", tests_blob))
            print(f"  {p}::{n}  {'tests only: '+str(t) if t else 'NO references at all'}")
```

Then **confirm each against the URL conf** before calling it dead — an unrouted view
exported from a package `__init__` that exists "for routing compatibility" is dead; a
helper called dynamically is not.

### Frontend

```bash
TPL=$(git ls-files "*.html" | xargs -n1 dirname | sort -u)   # EVERY template dir, incl. admin overrides
grep -rho "aria-\|role=\|<label\|alt=" $TPL | sort | uniq -c            # a11y
grep -rn "|safe" $TPL                    # then TRACE each to its source
grep -rho "onclick=" $TPL | wc -l        # CSP blockers
grep -rho "innerHTML" project/*/static/js/ | wc -l       # check each for escaping
grep -rho "integrity=" $TPL | wc -l      # SRI on external scripts
grep -rhoE 'https://[a-z0-9.-]+\.(com|net|io)' project/*/templates/base.html | sort -u
grep -rhoE '\bvar ' project/*/static/js/ | wc -l         # vs let/const
```

For every `|safe`, **trace the variable to whoever can set it** and check whether that
endpoint is privilege-restricted. Admin-authored rich text is a different finding from
teacher-supplied free text. Count `role=`/`aria-*` against the number of modals, tabs and
custom widgets — zero `role=` across 75 templates with several modal dialogs is a finding.

### Security

```bash
grep -nE "^(DEBUG|ALLOWED_HOSTS|SECURE_|SESSION_COOKIE_|CSRF_)" project/project/settings.py
grep -n "MIDDLEWARE = \[" -A 18 project/project/settings.py       # order matters
grep -rhoE "uses: [^@]+@[a-f0-9]{40}" .github/workflows/ | wc -l  # SHA-pinned
grep -rhoE "uses: [^@]+@v?[0-9.]+$" .github/workflows/ | sort -u  # tag-pinned = mutable
grep -nE "^USER|useradd" Dockerfile                               # non-root
git ls-files | grep -E "\.env$|credentials|\.pem$|\.key$"         # tracked secrets
```

Read any external script the app loads and ask whether SRI is possible. A CDN script
plus `script-src 'unsafe-inline'` means a CDN compromise is full execution in an
authenticated session — worth stating plainly, along with what raises the stakes (e.g.
personal data about minors).

### DevOps & CI/CD

The pipeline is code too, and it usually holds more privilege than the application does —
a compromised workflow has a repo-write token and, often, cloud credentials. Read every
workflow file in full; on the reference project the largest was **703 lines**, bigger than
all but four application files.

**Workflow map, jobs and triggers:**

```bash
for f in .github/workflows/*.yml; do
  trig=$(grep -A 6 "^on:" "$f" | grep -oE "^\s+(push|pull_request|schedule|workflow_dispatch|workflow_run|release):" | tr -d ' :' | tr '\n' ',' | sed 's/,$//')
  printf "  %-26s jobs=%-2s triggers=%s\n" "$(basename $f)" \
    "$(grep -cE '^  [a-z][a-z0-9_-]*:$' "$f")" "${trig:-?}"
done
```

**Supply chain and least privilege:**

```bash
grep -rhoE "uses: [^@]+@[a-f0-9]{40}" .github/workflows/ | wc -l   # SHA-pinned (good)
grep -rhoE "uses: [^@]+@v?[0-9.]+$"   .github/workflows/ | sort -u # tag-pinned = mutable
for f in .github/workflows/*.yml; do
  printf "  %-26s permissions blocks: %s\n" "$(basename $f)" "$(grep -c '^\s*permissions:' "$f")"
done
grep -rhoE 'secrets\.[A-Z_]+' .github/workflows/ | sort -u          # full secret inventory
grep -rn "environment:" .github/workflows/*.yml                      # approval gates
```

**Script injection** — the highest-severity class in CI, because `${{ }}` is substituted
as *text* before the shell or JS parses the line, so a branch name or commit subject
containing a quote executes. Only interpolation **inside a `run:` or `script:` body**
counts; the same expression in `env:` or `with:` is safe:

```python
import glob, re
RISKY = re.compile(r"\$\{\{\s*(github\.event|steps\.|inputs\.|env\.[A-Z_]*(TITLE|BODY|MSG|MESSAGE|NAME|SUBJECT))")
for path in sorted(glob.glob(".github/workflows/*.yml")):
    lines = open(path, encoding="utf-8").read().split("\n")
    in_body, indent = False, 0
    for i, ln in enumerate(lines, 1):
        if re.search(r"^\s*(run:|script:)\s*[|>]?", ln):
            in_body, indent = True, len(ln) - len(ln.lstrip()); continue
        if in_body:
            if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent:
                in_body = False
            elif RISKY.search(ln):
                print(f"  INJECTABLE {path}:{i}  {ln.strip()[:78]}")
```

Report both numbers: injectable sinks, and total occurrences. "0 injectable, 59 total in
safe `env:`/`with:` position" is a much stronger statement than either alone.

**Credential blast radius** — check whether each pipeline holds only what it needs. The
reference project deliberately gives the unattended nightly deploy an SSH key and *no*
cloud credential, while production uses Workload Identity Federation behind a required
reviewer:

```bash
for f in .github/workflows/deploy-*.yml; do
  printf "  %-32s wif=%s ssh=%s gcloud=%s\n" "$(basename $f)" \
    "$(grep -c 'workload_identity_provider' "$f")" "$(grep -ci 'ssh' "$f")" \
    "$(grep -c 'google-github-actions' "$f")"
done
```

**Container and image build:**

```bash
grep -c '^FROM' Dockerfile                                  # multi-stage?
grep -E '^FROM' Dockerfile                                  # pinned by tag or by digest?
grep -E '^FROM .*@sha256:' Dockerfile | wc -l               # digest-pinned count
grep -E '^USER' Dockerfile                                  # non-root?
grep -c '^HEALTHCHECK' Dockerfile
grep -c 'rm -rf /var/lib/apt/lists' Dockerfile              # layer hygiene
```

A project that SHA-pins all its Actions but leaves `FROM python:3.12-slim` unpinned has an
**inconsistent supply-chain posture** — the tag is mutable and rebuilds are not
reproducible. Cross-check against Dependabot's ecosystem list: `pip` + `github-actions`
but no `docker` means nothing is watching the base image at all.

**Scheduled work and drift:**

```bash
grep -c '"task"' project/project/celery.py                       # Beat entries
ls project/*/management/commands/*.py | grep -v __init__ | wc -l # wrapper commands
grep -c "package-ecosystem" .github/dependabot.yml
ls .github/dependabot.yml .github/CODEOWNERS 2>/dev/null
```

Where production runs scheduled jobs by a different mechanism than local development
(cloud scheduler vs Celery Beat), **verify parity explicitly** — every scheduled task
needs whatever wrapper the production mechanism invokes, or it silently never runs. Also
check deploy ordering: if migrations run as a separate job from the service rollout, new
code can meet an old schema mid-deploy.

**What to look for beyond the counts:** is there a rollback path, or only forward
deploys? Does anything verify a deploy actually landed (a version endpoint compared
against the source of truth)? Are deploys reproducible from a tag, or do they depend on
whatever was in the working tree? Is any deploy step both unattended *and* privileged?

### Testing

```bash
ls project/tests/unit/*.py project/tests/integration/*.py | grep -v __init__ | wc -l
grep -n "omit" -A 10 pyproject.toml     # what is EXCLUDED = the blind spot
make test 2>&1 | grep -E "^TOTAL|Total coverage|passed"
grep -rl "unittest.mock\|monkeypatch" project/tests/ | wc -l
```

**The `omit` list is the most important thing here.** Anything excluded from coverage is
code nobody measures — check whether it is also code nobody *tests*, and whether it runs
in production (cron-invoked management commands absolutely do). Also count deprecation
warnings from the test run; a large cluster in one subsystem is a scheduled migration.

### Performance

```bash
grep -rn "Paginator\|paginate_by" --include="*.py" project/*/views/  # then find list views WITHOUT it
grep -rn "cache\.\(get\|set\)" --include="*.py" project/ | grep -v tests  # is the cache used at all?
grep -rn "get_config()" --include="*.py" project/ | grep -v tests | wc -l  # uncached singleton calls
```

Cross-check against the project's **own stated scale ceiling** (CLAUDE.md or the README
usually names one). An unpaginated list view is fine at 10 rows and a problem at the
documented 2,000. If the cache is configured but used by only one subsystem, say so.

### Consistency with the project's own rules

Read `CLAUDE.md` (or equivalent) **as a specification** and verify each documented
invariant still holds. Those files record rules that were true when written; drift
between the rule and the code is the single richest source of real findings. On the
reference project this surfaced a stale "known gap" in the payment spec that the code had
fixed two versions earlier, and a dead `QUARTERS` constant the docs still pointed at.

---

## Part D — Report structure

Keep it scannable. The user should be able to read the census, the general assessment and
the action list, then dip into sections on demand.

```markdown
# <Project> — Full Codebase Review

## Census
<TWO PARAGRAPHS MAXIMUM. Dense prose with the numbers inline — NOT the raw tables from
Part B. You ran those tables to get the data; the report distils them. Paragraph one:
total lines and files, the category split, then the language breakdown including config
(YAML, Makefile, Dockerfile, shell, TOML) and the test ratio. Paragraph two: the
structural inventory (apps, models, indexes, routes, migrations, commands, tasks, admin
registrations, templates, JS modules, workflows, make targets), the complexity
distribution, documentation density, and churn. Pure measurement — no judgement.

Worked example of the right density:

  **72,294 tracked lines across 397 files.** Half is application code (35,930 / 49.7%), a
  quarter tests (18,167 / 25.1%), and an unusually high 12.3% documentation (8,913). By
  language: Python 21,566 (app 19,654 across 128 files; migrations 1,912), HTML templates
  10,096 in 75 files, JavaScript 2,988 in 17 modules, CSS 1,280, plus 3,977 lines of
  config — YAML 2,532 in 14 files, shell 720, Makefile 458, TOML 177, Dockerfile 90 — and
  a 2,128-line lockfile. Tests-to-code is 0.51, or 0.92 against application Python alone.

  **Structure:** 4 apps, 20 models (all with explicit `db_table`, 30 indexes, 4
  constraints, zero `FloatField`), 109 routes, 31 migrations, 17 management commands, 18
  Celery tasks behind 10 Beat entries, 19 admin registrations, 75 templates, 17 JS
  modules, 8 CI workflows, 48 make targets. 538 functions, median 13 lines, but 15 over
  100 — five of the nine longest in one file. 24.1% of application code is comments or
  docstrings. Across 233 commits, `settings.py` has changed 87 times.>

## General assessment
<2–4 paragraphs. What kind of project this is. What is genuinely strong, specifically.
Then THE DOMINANT WEAKNESS — the pattern behind the findings, named. End with an overall
grade and one sentence on what holds it back.>

## 1. Architecture — **grade**
## 2. Database — **grade**
## 3. Backend / business logic — **grade**
## 4. Frontend — **grade**
## 5. Security — **grade**
## 6. Testing — **grade**
## 7. DevOps & CI/CD — **grade**
## 8. Infrastructure & operations — **grade**
## 9. Code quality & maintainability — **grade**
## 10. Performance & scalability — **grade**

<Each section OPENS WITH ONE PLAIN-LANGUAGE SENTENCE saying what the area is, written for
someone non-technical — the academy owner, a manager, a client. No jargon, no hedging, one
sentence in italics. Then: what is good (specific, measured), then "Concerns:" as a
bulleted list. Every concern names the file, states the consequence, and where useful how
it was proven. Order by impact, not by discovery order.

The intro sentences exist because these reports get forwarded to people who pay for the
work but do not write it. Suggested phrasings — adapt, do not copy verbatim:

| Section | Plain-language opener |
|---|---|
| Architecture | *How the code is organised into pieces, and whether those pieces have clear jobs.* |
| Database | *How information is stored, and whether the storage layer protects it from being corrupted.* |
| Backend | *The business rules — pricing, scheduling, who owes what — and whether they are applied consistently.* |
| Frontend | *What users actually see and click, and whether it is safe and usable.* |
| Security | *How the system keeps out people who should not get in, and limits what insiders can reach.* |
| Testing | *The automated checks that run before every change, and how much of the code they actually exercise.* |
| DevOps & CI/CD | *The machinery that turns a code change into a running system, and the guardrails around it.* |
| Infrastructure | *The servers, databases and backups the system runs on, and what happens when something breaks.* |
| Code quality | *How easy the code is for the next person to read, change and trust.* |
| Performance | *Whether it stays fast as the amount of data grows.* |
>

## The three things I'd do next
<Exactly three, highest value first. One sentence of what, one of why. Optionally a short
"in a quieter moment" line for the next tier.>

## Coverage of this review
<MANDATORY, and measured from B.0 — not a vague apology at the end. State, per bucket,
how many files were read in full vs skimmed vs covered only by structural probes. Then
name anything material that went unread, and say what a reader should not conclude from
its absence. Example:

  Read in full: 13/13 files over 500 lines; 31/54 in the 200-500 bucket.
  Structural probes only: the 160 files under 200 lines.
  Not examined: no load testing was run, so the scalability findings are read from code
  and the project's stated ceiling rather than measured.>
```

### Grading

Grades orient, they do not measure. Anchor them:

| Grade | Means |
|---|---|
| A | Exemplary — would hold up in a much larger codebase |
| A− / B+ | Solid and production-ready, with named gaps |
| B / B− | Works, but a known weakness will bite as it grows |
| C | Functional but under-engineered for its own stated requirements |
| D / F | Actively risky — data loss, security, or correctness exposure |

Grade against **the project's own goals and scale**, not a hypothetical enterprise
system. A 2,000-user internal tool does not need what a public SaaS needs, and saying
otherwise wastes the user's time.

### Adapting the sections

The ten above suit a server-rendered web app. Drop sections that do not apply rather
than padding them, and add ones that do (ML pipeline, mobile, CLI ergonomics, API
contract, data migration). Say which you chose if it is not obvious.

---

## Guarantees

- **Review the entire project, always.** Never scope to a diff, a commit range or a
  history window; never probe one app when the pattern is tree-wide. Run B.0 and report
  coverage against it.
- **Never fabricate a number.** Measure or omit.
- **Never claim a defect you have not proven** — or label it unverified and say what
  proof would take.
- **Never report a raw hit as a finding** without checking it is not historical,
  commented, in prose, a near-miss identifier, or framework-registered.
- **Never let a green harness stand unquestioned** — ask what a broken run would look
  like.
- **Do not change any code.** This is read-only. Offer fixes at the end; do not apply
  them.
- **Say what you did not look at**, as a measured Coverage section keyed to B.0 — not a
  vague closing apology. An honest gap is useful; a confident inference is not.
- **Open every section with one plain-language sentence** naming what the area is, for a
  reader who does not write code. These reports get forwarded to whoever pays for the work.
- **Keep the census to two paragraphs.** Run the full tables to get the data, then distil —
  a wall of tables buries the finding.
- **Do not pad.** If an area is genuinely fine, three sentences and a grade is right.
