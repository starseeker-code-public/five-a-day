---
name: deploy
description: Use when the user wants to deploy Five a Day to GCP — the testing branch to the Compute Engine VM (http://34.26.130.187:8000/) and/or the main branch to Cloud Run production (https://fiveaday-332600671945.europe-southwest1.run.app/). Handles build, image-pinned Cloud Run jobs, migrations, and post-deploy version verification against GitHub. Triggers on "deploy", "ship to testing", "deploy to production", "push to prod".
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

PROD_URL=https://fiveaday-332600671945.europe-southwest1.run.app
TEST_URL=http://34.26.130.187:8000
```

**Image tags are the git short SHA** of the deployed commit (`git rev-parse --short HEAD`).
This convention matters: it is what lets you diff the deployed commit against the new one.

---

## Invocation

- `/deploy testing` → testing only
- `/deploy production` (or `prod` / `main`) → production only
- `/deploy` with no argument → **both, in sequence**: testing first, fully verified, then
  production. If testing fails at any point, STOP and do not touch production.

---

## Step 0 — Preflight (both targets)

Run these and stop on any failure. Never "fix" a failure by skipping the check.

```bash
gcloud auth list --filter=status:ACTIVE --format='value(account)'
```

Must be `hellofiveaday@gmail.com`. If nothing is active, tell the user to run
`gcloud auth login` — do not attempt the OAuth flow yourself.

```bash
git fetch origin --quiet
```

Then, for whichever branch this target uses (`testing` or `main`), enforce **strictly**:

1. Working tree is clean — `git status --porcelain` returns nothing.
2. Local branch is checked out and **identical to its remote**:
   `git rev-list --left-right --count <branch>...origin/<branch>` must be `0  0`.

If the branch is behind, `git pull --ff-only` and say so. If it is *ahead* or the tree is
dirty, **abort** and report exactly what diverges. Never deploy uncommitted work or a stale
checkout to the academy's live system — that is the whole point of the guard.

> Local `main` is frequently behind `origin/main`, because releases are merged on GitHub.
> Always deploy from `origin/main`, never a stale local copy.

---

## Step 1 — Deploy TESTING

### 1a. Pull and rebuild on the VM

The repo is owned by a different user than the SSH login, so every git call needs
`sudo git -c safe.directory=*`. Every docker call needs `sudo`.

```bash
gcloud compute ssh $VM_NAME --zone=$VM_ZONE --project=$PROJECT \
  --strict-host-key-checking=no --command='
    set -e
    D=/home/Proye/five-a-day
    sudo git -c safe.directory=* -C $D status --porcelain
    sudo git -c safe.directory=* -C $D pull --ff-only origin testing
    sudo git -c safe.directory=* -C $D log --oneline -1
    sudo docker system prune -f
    sudo docker compose -f $D/docker-compose.yml up -d --build
  '
```

If `status --porcelain` prints anything, the VM tree is dirty — stop and report it rather
than clobbering local changes with the pull.

**Memory warning.** The VM is an e2-micro: ~969 MB RAM with only ~380 MB free, plus 2 GB
swap. A build can OOM. The `docker system prune -f` above is not optional — it reclaims
space (disk sits around 58% of 30 GB). If the build is killed, retry once after
`sudo docker builder prune -af`; if it fails again, report it rather than looping.

The stack uses `restart: unless-stopped`, so containers return by themselves after a
reboot. `.env` is a symlink to `.env.testing` on the VM — never overwrite or recreate it.

### 1b. Verify

Go to Step 3. Expected version = `version` in `pyproject.toml` on `origin/testing`.

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

### 2b. Back up, then confirm migrations

Always take a backup before a production deploy, whether or not migrations are pending:

```bash
gcloud sql backups create --instance=$SQL_INSTANCE --project=$PROJECT
```

If the diff in 2a listed **any** migration files: show them and **ask the user to confirm**
before applying. Do not proceed on silence. If the diff is empty, skip the migrate job
entirely and say so.

### 2c. Build and push

```bash
gcloud builds submit --tag $IMAGE --project=$PROJECT .
```

This uploads the local working tree, which is safe: `.dockerignore` excludes `.env*`, and
`.gitignore` (which gcloud uses when there is no `.gcloudignore`) does too, so no local
secrets are uploaded or baked into the image.

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
for JOB in fiveaday-migrate fiveaday-birthday-emails fiveaday-generate-payments \
           fiveaday-expenses-daily fiveaday-expenses-monthly fiveaday-funfriday-emails \
           fiveaday-monthly-report fiveaday-payment-reminders; do
  gcloud run jobs update $JOB --image=$IMAGE --project=$PROJECT --region=$REGION
done
```

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
{"status": "healthy", "service": "fiveaday", "version": "1.15.1", "environment": "testing"}
```

Check all three fields:

- `status` is `healthy`
- `version` **equals** the `pyproject.toml` version on the corresponding GitHub branch
- `environment` is `testing` / `production` respectively

Cloud Run may need a few seconds and a cold start; retry the curl a few times before
concluding the version is wrong. The VM likewise needs the container to finish booting.

Report a table of expected vs actual for every target deployed. **If the versions do not
match, say so plainly and do not describe the deploy as successful.**

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

---

## Rollback

**Production** — instant, no rebuild. Shift traffic to the previous revision:

```bash
gcloud run revisions list --service=$SERVICE --project=$PROJECT --region=$REGION
gcloud run services update-traffic $SERVICE --project=$PROJECT --region=$REGION \
  --to-revisions=<PREVIOUS_REVISION>=100
```

Then point the 8 jobs back at the old image tag (same loop as 2d, with the old tag).

A rolled-back **schema** is not automatic. If the bad deploy applied migrations, restore the
backup from 2b — this loses any data written since, so confirm with the user first:

```bash
gcloud sql backups list --instance=$SQL_INSTANCE --project=$PROJECT
gcloud sql backups restore <BACKUP_ID> --restore-instance=$SQL_INSTANCE --project=$PROJECT
```

**Testing** — check out the previous commit on the VM and rebuild:

```bash
gcloud compute ssh $VM_NAME --zone=$VM_ZONE --project=$PROJECT --command='
  D=/home/Proye/five-a-day
  sudo git -c safe.directory=* -C $D checkout <PREVIOUS_SHA>
  sudo docker compose -f $D/docker-compose.yml up -d --build
'
```

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

**Never deploy production from a local `main`** that differs from `origin/main`.

---

## Reporting

Finish with a table: target, branch, commit SHA, expected version, `/health/` version, match
or not. State explicitly whether migrations ran, whether a backup was taken, and — if
anything was skipped or failed — exactly what and why. Do not report success unless the
version check passed for every target deployed.
