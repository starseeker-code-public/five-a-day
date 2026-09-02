---
name: deploy
description: Use when the user wants to deploy Five a Day to GCP — the testing branch to the Compute Engine VM (http://34.26.130.187:8000/) and/or the main branch to Cloud Run production (https://fiveaday-332600671945.europe-southwest1.run.app/). Always asks first which target to deploy (testing only, production only, or both). Handles build, image-pinned Cloud Run jobs, migrations, and post-deploy version verification against GitHub. Triggers on "deploy", "ship to testing", "deploy to production", "push to prod".
---

# deploy

Deploys Five a Day to GCP. Two targets, very different risk levels:

| Target | Branch | Where | URL |
|---|---|---|---|
| **testing** | `testing` | Compute Engine e2-micro, Docker Compose | <http://34.26.130.187:8000/> |
| **production** | `main` | Cloud Run + Cloud SQL | <https://fiveaday-332600671945.europe-southwest1.run.app/> |

Production serves a real academy. Treat every production step as irreversible unless you
have verified the rollback path.

---

## Constants

Use these verbatim. Do NOT read them from `gcloud config` — no default project is set on
this machine, so every command must pass `--project` explicitly.

```bash
PROJECT=five-a-day-evolution          # NOT "fiveaday"; the URL's 332600671945 is the project NUMBER
REGION=europe-southwest1
SERVICE=fiveaday
SQL_INSTANCE=fiveaday-db
IMAGE_BASE=europe-southwest1-docker.pkg.dev/five-a-day-evolution/fiveaday/web

VM_NAME=fiveaday-testing
VM_ZONE=us-east1-c                    # DEPLOYMENT.md says us-east1-b — that is WRONG
VM_PATH=/home/Proye/five-a-day        # owned by user "Proye", not the SSH login user

# The testing VM is a TWO-FILE compose stack. Both files, every time, in this order.
COMPOSE="-f $VM_PATH/docker-compose.yml -f $VM_PATH/docker-compose.testing.yml"

PROD_URL=https://fiveaday-332600671945.europe-southwest1.run.app
TEST_URL=http://34.26.130.187:8000

# Optional. Enables the row-count fingerprint on /health/?deep=1 (Step 2b/4).
# Read it from Secret Manager rather than hard-coding it:
#   HEALTH_PROBE_TOKEN=$(gcloud secrets versions access latest \
#     --secret=HEALTH_PROBE_TOKEN --project=$PROJECT 2>/dev/null)
# If it is not configured, the deep probe still reports connectivity and
# migration state — but you CANNOT prove the data survived. Say so explicitly in
# the Step 4 report instead of quietly reconciling fewer fields.
```

**Image tags are the git short SHA** of the deployed commit (`git rev-parse --short HEAD`).
This convention matters: it is what lets you diff the deployed commit against the new one.

---

## Invocation — ALWAYS ask which target first

**The first thing this skill does, every single time, is ask the user to pick the target
with `AskUserQuestion`.** There is no inferred default and no silent path to production:
the choice is always made by hand, on the spot, even when the invocation already named a
target and even when the answer seems obvious from the conversation.

Ask before Step 0 — before the auth check, before the backup check, before any `gcloud`
call. Nothing in this document runs until the answer is in.

```
Question: "¿Qué despliegue quieres hacer?"
Header:   "Deploy"
Options:
  1. "Solo testing"      — testing branch → Compute Engine VM (http://34.26.130.187:8000/).
                           Reversible, no real data at risk.
  2. "Solo producción"   — main branch → Cloud Run + Cloud SQL. Serves the real academy.
  3. "Testing y producción" — both, in sequence: testing first and fully verified, then
                           production. If testing fails at any point, STOP and do not touch
                           production.
```

Rules for the question:

- If the invocation named a target (`/deploy testing`, `/deploy prod`, …), put that option
  **first** and mark it `(Recomendado)` — but still ask. A typed argument is a suggestion,
  never the decision.
- Never add a fourth option that skips or reorders the verification steps.
- If the user answers anything other than these three (via "Other"), do not improvise a
  target: restate the three and ask again.

Whatever is chosen, run only the steps for that target and report on that target only.

---

## Step 0 — Preflight (both targets)

Run these and stop on any failure. Never "fix" a failure by skipping the check.

```bash
gcloud auth list --filter=status:ACTIVE --format='value(account)'
```

Must be `hellofiveaday@gmail.com`. If nothing is active, tell the user to run
`gcloud auth login` — do not attempt the OAuth flow yourself.

### Backup health — check this first, before anything else

Every other safety net in this document assumes a restore is possible. Confirm
that **before** you touch anything: deploying on top of a broken backup chain is the
one situation with no way back.

```bash
gcloud sql instances describe $SQL_INSTANCE --project=$PROJECT \
  --format='yaml(settings.backupConfiguration, settings.deletionProtectionEnabled)'

gcloud sql backups list --instance=$SQL_INSTANCE --project=$PROJECT --limit=1 \
  --format='value(id,status,type,windowStartTime)'
```

All four must hold — stop and report if any does not:

| Must be true | Why |
|---|---|
| `enabled: true` | no automated backups means no nightly recovery point |
| `pointInTimeRecoveryEnabled: true` | PITR is what gives sub-day granularity |
| `deletionProtectionEnabled: true` | **deleting the instance deletes every backup with it** |
| newest backup `SUCCESSFUL` and < 48h old | a silently failing backup job looks identical to a working one |

This check is read-only and takes seconds. Run it even for a deploy you are sure
about — its value is precisely on the day something has quietly broken.

```bash
git fetch origin --quiet
```

Then, for whichever branch this target uses (`testing` or `main`), enforce **strictly**:

1. Working tree is clean — `git status --porcelain` returns nothing.
2. The local branch is **identical to its remote**:
   `git rev-list --left-right --count <branch>...origin/<branch>` must be `0  0`.

If the branch is behind, fast-forward it and say so. If it is *ahead* or the tree is dirty,
**abort** and report exactly what diverges. Never deploy uncommitted work or a stale
checkout to the academy's live system — that is the whole point of the guard.

**Checkout matters for production, not for testing.** The two targets consume the branch
differently, so choosing "Testing y producción" is *not* asking you to check out two branches
at once:

- **testing** — the VM pulls `origin/testing` itself (Step 1a), so your local checkout is
  irrelevant and only the remote-sync check above applies. Fast-forward without switching:
  `git fetch origin testing:testing`.
- **production** — Step 2c builds **the tree that is checked out locally**, so `main` MUST be
  checked out and equal to `origin/main` before you build. See the assertion in 2c.

Record the branch you started on (`git rev-parse --abbrev-ref HEAD`) and check it back out
when you are done, so the deploy leaves no local state behind.

> Local `main` is frequently behind `origin/main`, because releases are merged on GitHub.
> Always deploy from `origin/main`, never a stale local copy.

---

## Step 1 — Deploy TESTING

### 1a. Pull and rebuild on the VM

The repo is owned by a different user than the SSH login, so every git call needs
`sudo git -c safe.directory=*`. Every docker call needs `sudo`.

```bash
gcloud compute ssh $VM_NAME --zone=$VM_ZONE --project=$PROJECT --quiet \
  --strict-host-key-checking=no --command='
    set -e
    D=/home/Proye/five-a-day
    # HARD GATE: a dirty tree must abort BEFORE the pull, not merely be printed after it.
    if [ -n "$(sudo git -c safe.directory=* -C $D status --porcelain)" ]; then
      echo "ABORT: VM working tree is dirty:"
      sudo git -c safe.directory=* -C $D status --porcelain
      exit 1
    fi
    sudo git -c safe.directory=* -C $D pull --ff-only origin testing
    sudo git -c safe.directory=* -C $D log --oneline -1
    sudo docker system prune -f
    sudo docker compose -f $D/docker-compose.yml -f $D/docker-compose.testing.yml up -d --build

    # HARD GATE: prove the testing overlay actually took effect. A one-file
    # bring-up starts a valid stack on the WRONG volume and exits 0, so the
    # only reliable signal is the mount itself. Never skip or soften this.
    VOL=$(sudo docker inspect fiveaday_postgres --format "{{range .Mounts}}{{if .Name}}{{.Name}} {{end}}{{end}}")
    case "$VOL" in
      *testing_postgres_data*) echo "GATE OK: db mounted $VOL" ;;
      *) echo "ABORT: wrong DB volume mounted: [$VOL] — expected *testing_postgres_data."
         echo "The real data is intact in an orphaned volume. See Troubleshooting."
         exit 1 ;;
    esac

    # A dangling *_testing_postgres_data means the stack came up beside the real
    # data rather than on it.
    if sudo docker volume ls -f dangling=true --format "{{.Name}}" | grep -q testing_postgres_data; then
      echo "ABORT: five-a-day_testing_postgres_data is dangling — wrong stack is live."
      exit 1
    fi
  '
```

**Both `-f` files are mandatory — omitting the overlay silently swaps the database.**
`docker-compose.testing.yml` is not cosmetic. It overrides the `db` service to mount
`testing_postgres_data` instead of the base file's `postgres_data`, and it replaces
`runserver` with Gunicorn. Bring the stack up with only `docker-compose.yml` and Compose
happily starts a **different, valid, wrong** stack: the DB attaches to the *dev* volume,
which on this VM is a months-old snapshot. Nothing errors. `/health/` reports the correct new
version, because the *code* did deploy — only the data is wrong. The real testing volume is
left orphaned but intact, so the damage is recoverable (see Troubleshooting), but you will not
notice it from the version check alone.

Symptoms that this happened: `/health/` is correct, yet the site shows far fewer students,
payments or QA backlog tasks than it did before the deploy, and `sudo docker volume ls`
shows a dangling `five-a-day_testing_postgres_data`.

The dirty check **must** be a gate that exits non-zero — not a bare `status --porcelain` line.
`git status --porcelain` exits 0 on a dirty tree, so under `set -e` a bare call stops nothing:
the `pull` runs on the very next line and the local changes are already clobbered by the time
you read the output. If the gate fires, report it and stop — never re-run with the check
removed.

`--quiet` is required. On a machine with no existing SSH key, `gcloud compute ssh` prompts
interactively to generate one, which hangs a non-interactive run.

**Memory warning.** The VM is an e2-micro: ~969 MB RAM with only ~380 MB free, plus 2 GB
swap. A build can OOM. The `docker system prune -f` above is not optional — it reclaims
space (disk sits around 58% of 30 GB). If the build is killed, retry once after
`sudo docker builder prune -af`; if it fails again, report it rather than looping.

The stack uses `restart: unless-stopped`, so containers return by themselves after a
reboot. `.env` is a symlink to `.env.testing` on the VM — never overwrite or recreate it.

`up -d --build` recreates **every** service in the compose file, including
`fiveaday_postgres`. The data survives (it lives in a named volume), but expect the database
container to restart, and expect the web container to spend a minute on migrations and
`collectstatic` before it listens. `/health/` returns an empty reply during that window —
retry for a couple of minutes before concluding the deploy failed.

### 1b. Verify

Go to Step 3. Expected version = `version` in `pyproject.toml` on `origin/testing`.

The version check alone is **not** sufficient on the VM — it passes even when the stack came
up on the wrong database volume (see 1a). Confirm the stack mounted the testing volume and
that the data is still there:

```bash
gcloud compute ssh $VM_NAME --zone=$VM_ZONE --project=$PROJECT --quiet --command='
  echo "--- volume the db actually mounted (want: five-a-day_testing_postgres_data) ---"
  sudo docker inspect fiveaday_postgres \
    --format "{{range .Mounts}}{{.Name}}{{println}}{{end}}"
  echo "--- no volume should be dangling ---"
  sudo docker volume ls -f dangling=true --format "{{.Name}}" | grep postgres || echo "none (good)"
  U=$(sudo docker exec fiveaday_postgres printenv POSTGRES_USER)
  D=$(sudo docker exec fiveaday_postgres printenv POSTGRES_DB)
  echo "--- row counts (compare against what you saw before the deploy) ---"
  sudo docker exec fiveaday_postgres psql -U "$U" -d "$D" -At -F"|" -c \
    "select relname, n_live_tup from pg_stat_user_tables where n_live_tup>0
     order by n_live_tup desc limit 10;"
'
```

---

## Step 2 — Deploy PRODUCTION

### 2a. Resolve versions and detect migrations

```bash
NEW_SHA=$(git rev-parse --short origin/main)
IMAGE=$IMAGE_BASE:$NEW_SHA

OLD_SHA=$(gcloud run services describe $SERVICE --project=$PROJECT --region=$REGION \
  --format='value(spec.template.spec.containers[0].image)' | sed 's/.*://')
```

Because image tags are git SHAs, unapplied migrations are just a git diff:

```bash
git diff --name-only $OLD_SHA..$NEW_SHA -- '*/migrations/*.py'
```

Show the user the old version, the new version, and this migration list.

### 2b. Back up, then confirm — before building anything

Always take a backup before a production deploy, whether or not migrations are pending —
and then **prove it succeeded**. An unverified backup is not a rollback plan; the whole
safety argument for touching production rests on this one artifact existing.

```bash
gcloud sql backups create --instance=$SQL_INSTANCE --project=$PROJECT

BACKUP_ID=$(gcloud sql backups list --instance=$SQL_INSTANCE --project=$PROJECT \
  --limit=1 --format='value(id)')
BACKUP_STATUS=$(gcloud sql backups describe $BACKUP_ID --instance=$SQL_INSTANCE \
  --project=$PROJECT --format='value(status)')

echo "backup $BACKUP_ID -> $BACKUP_STATUS"
[ "$BACKUP_STATUS" = "SUCCESSFUL" ] || { echo "ABORT: backup is $BACKUP_STATUS, not SUCCESSFUL"; exit 1; }
```

**Hard rule: no migration and no rollout without a `SUCCESSFUL` backup id in hand.** If the
backup fails, stop and report it. Do not proceed "just this once" because the change looks
small — a failed migration with no backup is the one unrecoverable state in this whole
procedure. Carry `$BACKUP_ID` through to the Step 4 report.

This deploy backup is an **on-demand** backup, which Cloud SQL exempts from the automated
retention count — it will not age out on its own. `scripts/backup_retention.sh` caps these at
the 3 most recent so they do not pile up, so do not hand-delete them here.

### Capture the pre-deploy fingerprint

Record what production looks like *before* you change anything, so Step 4 has something to
reconcile against. Without this, a deploy that lands on the wrong data is indistinguishable
from one that worked:

```bash
curl -s -H "X-Probe-Token: $HEALTH_PROBE_TOKEN" "$PROD_URL/health/?deep=1"
```

Keep the whole response. The fields that matter are `version`, `database.connected`,
`database.applied_migrations` and, when the token is configured, `database.counts`. If
`counts` is absent, note that the data-loss check will be unavailable in Step 4 — do not
silently drop it from the report.

If the diff in 2a listed **any** migration files: show them and **ask the user to confirm
now**, before Step 2c. Do not proceed on silence. If the diff is empty, skip the migrate job
entirely and say so.

**Get that confirmation here, not at 2e.** Everything from 2c onward is externally visible:
2c publishes an image to Artifact Registry and 2d repoints all the Cloud Run Jobs at it (11 as of v1.26.0). Defer the
question to the migrate step and a declining user leaves you having already published an
image and pinned 8 production jobs to code that never rolled out.

### 2c. Build and push

`gcloud builds submit` uploads **the working tree checked out right now** — it does not read
`origin/main`. But `$IMAGE` is tagged with `origin/main`'s SHA, so if HEAD is anything else
you publish one branch's code under another branch's tag. Check out `main` and assert the
match first. This is not optional:

```bash
git checkout main
git pull --ff-only origin main
[ "$(git rev-parse --short HEAD)" = "$NEW_SHA" ] || { echo "ABORT: HEAD != origin/main"; exit 1; }
git status --porcelain    # must be empty
```

Skip it and you ship whatever you happened to be working on — usually `development` — to the
academy as `$NEW_SHA`. It also permanently breaks the SHA→image mapping that 2a's migration
diff relies on, because the tag no longer describes the code inside the image.

```bash
gcloud builds submit --tag $IMAGE --project=$PROJECT .
```

Uploading the working tree is safe: `.dockerignore` excludes `.env*`, and `.gitignore` (which
gcloud uses when there is no `.gcloudignore`) does too, so no local secrets are uploaded or
baked into the image.

If the tag already exists in Artifact Registry, the image was built previously and only the
rollout is missing — skip the build and go straight to 2d. Check with:

```bash
gcloud artifacts docker tags list $IMAGE_BASE --project=$PROJECT
```

### 2d. Update ALL Cloud Run jobs — do not skip this

**This is the single most important step, and DEPLOYMENT.md does not mention it.** Each of
the 8 Cloud Run jobs pins its own image tag. Deploying the service without updating them
leaves `fiveaday-migrate` applying the *old* migration set and every scheduled task running
last release's code indefinitely.

Re-check the list first, in case a job was added since this skill was written:

```bash
gcloud run jobs list --project=$PROJECT --region=$REGION
```

```bash
for JOB in $(gcloud run jobs list --project=$PROJECT --region=$REGION --format='value(metadata.name)'); do
  gcloud run jobs update $JOB --image=$IMAGE --project=$PROJECT --region=$REGION
done
```

Then **verify the pins actually moved** — this step exists to prevent drift, so confirming it
is part of the step, not an extra. Note the doubled `template` in the field path: a job's
image lives at `spec.template.spec.template.spec...`, and the wrong path prints empty for
every job, which reads exactly like a clean result.

```bash
for JOB in $(gcloud run jobs list --project=$PROJECT --region=$REGION --format='value(metadata.name)'); do
  printf '%-32s ' "$JOB"
  gcloud run jobs describe $JOB --project=$PROJECT --region=$REGION \
    --format='value(spec.template.spec.template.spec.containers[0].image)' | sed 's/.*://'
done
```

Every listed job (11 as of v1.26.0) must print `$NEW_SHA` before you continue.

Then assert every job is attached to **the** database. A job pointed at another instance
would apply migrations somewhere invisible and still exit 0:

```bash
for JOB in $(gcloud run jobs list --project=$PROJECT --region=$REGION --format='value(metadata.name)'); do
  printf '%-32s ' "$JOB"
  gcloud run jobs describe $JOB --project=$PROJECT --region=$REGION \
    --format='value(spec.template.metadata.annotations."run.googleapis.com/cloudsql-instances")'
done
```

Every line must read `$PROJECT:$REGION:$SQL_INSTANCE` — i.e.
`five-a-day-evolution:europe-southwest1:fiveaday-db`. Anything else, or an empty line, stops
the deploy. Do not "fix" it by re-running the update; find out why the annotation moved.

### 2e. Migrate BEFORE the service rolls

DEPLOYMENT.md shows migrate *after* the deploy. That is backwards — it leaves new code
hitting an old schema during the rollout window. Run it first:

```bash
gcloud run jobs execute fiveaday-migrate --project=$PROJECT --region=$REGION --wait
```

Only run this if 2b found migrations and the user confirmed.

### 2f. Roll out the service

```bash
gcloud run deploy $SERVICE --image=$IMAGE --project=$PROJECT --region=$REGION
```

**Pass `--image` and nothing else.** The service carries ~30 env vars plus 6 Secret Manager
refs (`DJANGO_SECRET_KEY`, `DATABASE_URL`, `EMAIL_SECRET`, `GOOGLE_CLIENT_SECRET`, …).
Adding `--set-env-vars` replaces the whole set and silently drops everything omitted.
Cloud Run performs a zero-downtime rolling update.

---

## Step 3 — Verify the deployed version (both targets)

`/health/` returns the running version, so confirm the deploy actually landed rather than
assuming it did. Cross-check against GitHub, which is the source of truth.

```bash
# Expected — read from the branch on GitHub, not the local working tree
git show origin/testing:pyproject.toml | grep '^version'   # testing
git show origin/main:pyproject.toml    | grep '^version'   # production

# Actual
curl -s $TEST_URL/health/
curl -s $PROD_URL/health/
```

Each returns:

```json
{"status": "healthy", "service": "fiveaday", "version": "<pyproject version>", "environment": "testing"}
```

That literal is illustrative — the expected value is always whatever `pyproject.toml` says on
the branch you deployed, never a version hard-coded here. Read it from the branch, not from
the tip commit's *title*: a release merge can leave `main` titled `v1.15.1` while the file
already says `1.15.2`.

Check all three fields:

- `status` is `healthy`
- `version` **equals** the `pyproject.toml` version on the corresponding GitHub branch
- `environment` is `testing` / `production` respectively

Cloud Run may need a few seconds and a cold start; retry the curl a few times before
concluding the version is wrong. The VM likewise needs the container to finish booting.

Report a table of expected vs actual for every target deployed. **If the versions do not
match, say so plainly and do not describe the deploy as successful.**

### What `/health/` cannot tell you

`health_check` (`core/views/errors.py`) returns `settings.APP_VERSION` and
`settings.ENVIRONMENT` and **never touches the database**. It is a code-and-config probe, not
a data probe. A green `/health/` with the right version therefore proves only that the new
image is running — it says nothing about *which database that image is talking to*, or
whether the database is reachable at all.

This is exactly how a testing deploy once reported a correct 1.15.2 while serving a
months-old database: the code deployed fine, only the volume was wrong. Treat the version
check as necessary and **not** sufficient:

- **testing** — pair it with the volume + row-count assertions in 1a and 1b.
- **production** — pair it with the Cloud SQL attachment assertion below.

```bash
gcloud run services describe $SERVICE --project=$PROJECT --region=$REGION \
  --format='value(spec.template.metadata.annotations."run.googleapis.com/cloudsql-instances")'
```

Must print `$PROJECT:$REGION:$SQL_INSTANCE`. Also confirm the DB still arrives by secret
reference rather than as a literal, which is what a bad `--set-env-vars` would leave behind:

```bash
gcloud run services describe $SERVICE --project=$PROJECT --region=$REGION --format=json \
  | grep -A2 '"DATABASE_URL"'
```

It must show a `secretKeyRef`, and there must be **no** `POSTGRES_HOST` / `POSTGRES_DB` env
vars on the service. Production deliberately has only `DATABASE_URL`; `settings.py` falls
back to `POSTGRES_*` (defaulting to `localhost/fiveaday_db`) whenever `DATABASE_URL` is empty,
and on Cloud Run that fallback cannot connect — the revision fails to become ready and traffic
stays on the old one. That is a loud failure rather than a silent wrong-database, which is why
production is *not* exposed to the testing failure mode. Keep it that way: never add
`POSTGRES_*` variables to the Cloud Run service, because that would turn a crash into a
silently-wrong database.

### The APP_VERSION trap

`settings.py` has `APP_VERSION = os.getenv("APP_VERSION", "<pyproject version>")`, so an
`APP_VERSION` env var **overrides** the baked-in value and makes `/health/` lie. Neither
environment sets it today (verified). If a mismatch appears and the image is definitely
current, check for the override before concluding the deploy failed:

```bash
gcloud run services describe $SERVICE --project=$PROJECT --region=$REGION \
  --format='value(spec.template.spec.containers[0].env)' | tr ',' '\n' | grep -i APP_VERSION

gcloud compute ssh $VM_NAME --zone=$VM_ZONE --project=$PROJECT \
  --command='sudo grep APP_VERSION /home/Proye/five-a-day/.env.testing'
```

### Repairing a single env var — and the retired `ACADEMY_IBAN_HOLDER` corruption

Production reads its env from Cloud Run, never from `.env.production`, and the `ACADEMY_*`
values were set by hand. Nothing in the repo provisions them, so nothing re-asserts them
either — a bad value stays bad across every future deploy.

**RESOLVED (2026-09-01): `ACADEMY_IBAN_HOLDER` is now deliberately ASCII —
`Silvia Yubitza Moreno Carlin`, plain `i` — everywhere: the Cloud Run service AND all four
local `.env*` files. Do NOT "repair" it back to `Carlín`.** The accented value was corrupted
to `Carl?n` twice by console-codepage transcoding (any `--update-env-vars` from a
non-UTF-8 Windows shell re-serialises the whole env set and re-corrupts it, and a paste
into a legacy console can mangle the `í` before gcloud even runs). Accepting the plain `i`
removes the last non-ASCII byte from the env set, so no future update from any shell can
corrupt anything. The GDPR legal footer in `base_email.html` keeps the accented legal name —
it is file-based UTF-8 rendered by Django and cannot transcode.

To change any single env var, use **`--update-env-vars`**, which merges:

```bash
gcloud run services update $SERVICE --project=$PROJECT --region=$REGION \
  --update-env-vars="^@^SOME_VAR=some value, with commas"
```

Three things matter here:

- **`--update-env-vars` merges; `--set-env-vars` replaces.** Never reach for `--set-env-vars`
  to change one value — it drops the ~30 vars and Secret Manager refs you did not repeat.
- **A non-ASCII value needs a UTF-8 shell** (the Bash tool qualifies; a legacy-codepage
  cmd/PowerShell console does not) — and read the bytes back afterwards, because gcloud
  says "deployed" either way. Today every env value is ASCII; keep it that way, or accept
  owning this failure mode for the value you add.
- **The `^@^` prefix sets `@` as the delimiter** so spaces and commas in the value are safe.

Snapshot before and diff after — the whole risk of an env change is the vars you did not
mean to touch:

```bash
gcloud run services describe $SERVICE --project=$PROJECT --region=$REGION --format=json \
  | python -c "import json,sys; e=json.load(sys.stdin)['spec']['template']['spec']['containers'][0]['env']; \
print(len(e)); [print(x['name'], '=', repr(x.get('value')) if 'value' in x else '<secretKeyRef>') for x in sorted(e, key=lambda k: k['name'])]"
```

The entry count must be identical before and after (37 total as of 2026-09-01, including 7
`<secretKeyRef>` entries — recount rather than trust this snapshot). Verifying the value
reached the container needs a request path that prints it — the payment-reminder form at
`/apps/payment-reminder/` renders `{{ iban_holder }}` in its preview.

This rolls a new revision (zero-downtime). Roll back by retargeting traffic to the previous
revision, per **Rollback** below.

---
---

## Step 4 — Reconcile production, and stop if anything is odd

Step 3 proves the right *image* is serving. This step proves it is serving the right
*data*. Run the deep probe again and compare it against the pre-deploy fingerprint from 2b:

```bash
curl -s -H "X-Probe-Token: $HEALTH_PROBE_TOKEN" "$PROD_URL/health/?deep=1"
```

Reconcile every line, and report the table even when everything passes:

| Field | Passes when |
|---|---|
| `status` | `healthy` (a `degraded` 503 means the DB is unreachable) |
| `version` | equals `pyproject.toml` on `origin/main` |
| `environment` | `production` |
| `database.connected` | `true` |
| `database.unapplied_migrations` | `0` |
| `database.counts.*` | **>= the pre-deploy value for every key** |
| `cloudsql-instances` | `$PROJECT:$REGION:$SQL_INSTANCE` (Step 3) |

Counts only ever grow during a deploy window of a few minutes. **Any decrease in any count
is a red flag**, not a rounding detail — it means the release is talking to a different
database, or data was destroyed. A large *increase* is equally suspect: it suggests a
different database rather than a healthy one.

### If anything fails, stop and ask

Do not attempt a repair on your own initiative, and do not describe the deploy as successful.
Report exactly which field failed, with the before and after values, then **ask the user
which way to go** — presenting these three options and their consequences:

1. **Roll the service back** to the previous revision (recorded in Step 3). Instant, no
   rebuild, and it touches no data. This is the right first move for almost every failure,
   because it restores service while you diagnose.
2. **Restore the database** from `$BACKUP_ID`. Only for genuine data loss or a bad migration.
   **This discards everything written since the backup**, so it needs explicit confirmation
   naming the backup id — never infer it from a general "yes, fix it".
3. **Leave it and investigate** with the service still on the new revision, if the anomaly is
   cosmetic and the academy is unaffected.

Recommend one, but let the user choose. A schema rollback is not automatic: if migrations ran,
option 1 alone leaves new columns in place, which is usually harmless but must be stated.

---

## Rollback

**Production** — instant, no rebuild. Shift traffic to the previous revision:

```bash
gcloud run revisions list --service=$SERVICE --project=$PROJECT --region=$REGION
gcloud run services update-traffic $SERVICE --project=$PROJECT --region=$REGION \
  --to-revisions=<PREVIOUS_REVISION>=100
```

Then point the jobs back at the old image tag (same loop as 2d, with the old tag).

A rolled-back **schema** is not automatic. If the bad deploy applied migrations, restore the
backup from 2b — this loses any data written since, so confirm with the user first:

```bash
gcloud sql backups list --instance=$SQL_INSTANCE --project=$PROJECT
gcloud sql backups restore <BACKUP_ID> --restore-instance=$SQL_INSTANCE --project=$PROJECT
```

**Testing** — check out the previous commit on the VM and rebuild:

```bash
gcloud compute ssh $VM_NAME --zone=$VM_ZONE --project=$PROJECT --quiet --command='
  D=/home/Proye/five-a-day
  sudo git -c safe.directory=* -C $D checkout <PREVIOUS_SHA>
  sudo docker compose -f $D/docker-compose.yml -f $D/docker-compose.testing.yml up -d --build
'
```

---

## Production backups — what exists, and the export script

Know this before you rely on any of it.

| | |
|---|---|
| Automated | nightly **23:00 UTC**, kept **7** (a flat count, not an age) |
| Tier: biweekly | 1 on-demand tagged `tier:biweekly`, created on the 1st and 16th |
| Tier: monthly | 1 on-demand tagged `tier:monthly`, created on the last day of the month |
| Manual / deploy | on-demand, **3 most recent kept** |
| PITR | enabled, **7 days** of transaction logs |
| Stored in | `eu` **multi-region**, outside the instance's zone (`europe-southwest1-b`) |
| Instance | **ZONAL** — no HA replica, no automatic failover |
| Deletion protection | enabled |

Cloud SQL has no grandfather-father-son retention, so only the daily tier is native. The
biweekly and monthly tiers are on-demand backups created and pruned by
`scripts/backup_retention.sh` (tracked in the repo, safe to read, `--dry-run` supported).
Tiers cannot be created retroactively: a point only exists if the script ran that day.

**Two restore paths, and they are not the same:**

- `gcloud sql backups restore <ID> --restore-instance=$SQL_INSTANCE` **overwrites the
  instance in place** and discards everything written since that backup.
- PITR restores by **cloning to a NEW instance**
  (`gcloud sql instances clone ... --point-in-time`), leaving production untouched. Prefer
  this when you need to *inspect* rather than *replace*.

### `scripts/export_prod_db.sh` — may not be present

A full logical export (`.sql.gz`) of production to a local directory. It is **deliberately
gitignored and never pushed**, because the dump contains the personal data of real students
including minors. Access to it is a per-person decision by the maintainer.

**Check for it rather than assuming either way** — most of the time it will be there.
Being untracked means it travels with the machine instead of the repo, so on the maintainer's
machine, and on any machine they have granted it to, it is normally present and you should
treat it as a tool you have.

```bash
[ -x scripts/export_prod_db.sh ] && echo present || echo absent
```

It is missing only on a fresh clone, in CI, or for someone who was not given it. That is not a
broken checkout: do not recreate it and do not reconstruct it from this description — holding
it is a granted decision, and silently regenerating it would hand out production data access
that nobody approved. Report that it is missing and ask the maintainer.

When it *is* present, the destination is a **required argument with no default**, so a dump
can never land somewhere nobody chose:

```bash
./scripts/export_prod_db.sh --dest "<directory you choose>"
./scripts/export_prod_db.sh --dest "<dir>" --dry-run
```

It stages through a private GCS bucket, downloads, verifies the archive (valid gzip, core
tables actually present), then deletes the cloud copy so the only remaining copy is local. A
per-database export excludes cluster-wide roles, so restoring into a fresh instance means
recreating `fiveaday_user` first.

This export is **not** part of a normal deploy. Take one before genuinely irreversible work —
a destructive migration, a data backfill, an instance change — where the in-place Cloud SQL
restore path is itself at risk.

> `make backup` is **not** this. That target dumps the **local dev** database from the
> `db` container into `backups/`. It never touches production.

---

## Troubleshooting

**`PERMISSION_DENIED on resource project fiveaday`** — wrong project ID. It is
`five-a-day-evolution`.

**`detected dubious ownership`** on the VM — a `sudo git` call is missing
`-c safe.directory=*`.

**Cloud Run logs:**

```bash
gcloud run services logs tail $SERVICE --project=$PROJECT --region=$REGION
```

**Build OOM on the VM** — prune, then retry once (see 1a).

**Testing came up on the wrong database volume** (row counts collapsed, a dangling
`five-a-day_testing_postgres_data` appeared). The data is not lost — the old volume is
orphaned, not deleted, because `docker system prune -f` does **not** remove volumes (that
needs `--volumes`, which you must never add here). Recover by bringing the stack up with both
compose files:

```bash
gcloud compute ssh $VM_NAME --zone=$VM_ZONE --project=$PROJECT --quiet --command='
  set -e
  D=/home/Proye/five-a-day
  # Back the real volume up byte-for-byte before touching anything.
  sudo docker volume create testing_pg_backup
  sudo docker run --rm -v five-a-day_testing_postgres_data:/from:ro \
    -v testing_pg_backup:/to alpine sh -c "cp -a /from/. /to/"
  sudo docker compose -f $D/docker-compose.yml -f $D/docker-compose.testing.yml up -d --build
'
```

The entrypoint runs `migrate` on boot, so the pending migrations apply to the restored data
on the way up. Verify with the row counts in 1b before deleting the backup volume.

To inspect an orphaned volume without disturbing it, copy it and run a throwaway Postgres on
the **copy**:

```bash
sudo docker volume create probe
sudo docker run --rm -v <ORPHAN_VOLUME>:/from:ro -v probe:/to alpine sh -c 'cp -a /from/. /to/'
sudo docker run -d --name pg_probe -e POSTGRES_PASSWORD=probe \
  -e PGDATA=/var/lib/postgresql/data/pgdata \
  -v probe:/var/lib/postgresql/data/pgdata postgres:16-alpine
sudo docker exec pg_probe psql -U <POSTGRES_USER> -d <POSTGRES_DB> -c '\dt'
```

**Never run `docker volume prune` or `docker system prune --volumes` on the VM.** An orphaned
volume there is usually the only surviving copy of the testing data.

**Never deploy production from a local `main`** that differs from `origin/main`.

---

## Reporting

Finish with a table: target, branch, commit SHA, expected version, `/health/` version, match
or not. State explicitly which migrations ran, whether a backup was taken **and its id**, the
previous Cloud Run revision (the rollback target), and — if anything was skipped or failed —
exactly what and why. Do not report success unless the version check passed for every target
deployed.

A version match alone is not a green light. For **testing**, also report the mounted DB
volume and the row counts from 1b; for **production**, the `cloudsql-instances` value, the
verified `$BACKUP_ID`, and the Step 4 before/after reconciliation table. If you did not run
those assertions — or `HEALTH_PROBE_TOKEN` was unset so counts were unavailable — say so
plainly rather than implying the data was verified.

Then check the original branch back out (recorded in Step 0) and confirm `git status
--porcelain` is empty, so the deploy leaves no local state behind.
