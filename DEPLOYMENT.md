# Deployment Guide — Google Cloud Platform

Five a Day runs on three environments with different cost and complexity trade-offs.

**Live URLs**

| Environment | URL |
|-------------|-----|
| Production  | <https://fiveaday-332600671945.europe-southwest1.run.app/login/> |
| Testing (QA)| <http://34.26.130.187:8000/> |
| Development | <http://localhost:8000/> |

Production went live in **v1.14.7**. That release also carried the fix that made it boot:
the image's Gunicorn `CMD` needs `--chdir project`, because `manage.py` lives at
`/app/project/manage.py` and the settings package at `/app/project/project/`, so
`project.wsgi` does not resolve from `/app`. Development uses `runserver` and the testing VM
overrides the command, so production was the only environment that ran the image's own `CMD`
— it failed there with `ModuleNotFoundError: No module named 'project.wsgi'`.

---

## Environments at a Glance

|                   | Development         | Testing                        | Production                  |
|-------------------|---------------------|--------------------------------|-----------------------------|
| **Where**         | Local machine       | GCP Compute Engine (free tier) | GCP Cloud Run (`europe-southwest1`) |
| **Database**      | Docker (PostgreSQL) | Docker (PostgreSQL)            | Cloud SQL (PostgreSQL 16)   |
| **Celery**        | Docker (full stack) | Docker (full stack)            | Eager mode + Cloud Scheduler|
| **Static files**  | Django runserver    | WhiteNoise                     | WhiteNoise                  |
| **HTTPS**         | No                  | No                             | Cloud Run (automatic)       |
| **Cost**          | $0                  | **$0** (permanent free tier)   | ~$15–27/month               |

---

## 1. Development (Local)

No cloud resources needed. Everything runs in Docker, identical to production services.

```bash
uv sync --no-install-project   # Install dependencies
make up                         # Start PostgreSQL + Django + Redis + Celery
make test                       # Run test suite against PostgreSQL
```

---

## 2. Testing (Compute Engine — Free)

The testing environment runs on a **GCP Compute Engine e2-micro**, which is permanently free. It uses
the same `docker-compose.yml` as development — no cloud-native services needed.

### Why a VM instead of Cloud Run

- Free forever under GCP's always-free tier
- Identical to the local docker-compose — no surprises, no refactoring
- Acceptable to be slow or restart — dummy data only, no real users
- 30-40 students and 200 payments fit easily in 1 GB RAM with a swap file

### Limitations

- The e2-micro free tier requires a US region (`us-east1`, `us-west1`, or `us-central1`). From Spain
  this means ~150ms latency, which is acceptable for testing purposes.
- 1 GB RAM is tight with all five containers running. A swap file is required (see below).
- The testing database is completely isolated from production — different host, different credentials.

### VM setup

#### 1. Create the instance

```bash
gcloud compute instances create fiveaday-testing \
  --zone=us-east1-c \
  --machine-type=e2-micro \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-standard \
  --tags=http-server
```

#### 2. SSH in and install Docker

```bash
gcloud compute ssh fiveaday-testing --zone=us-east1-c
```

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
sudo systemctl enable docker
```

#### 3. Add a swap file (required)

1 GB RAM is not enough for all five containers plus the OS. A 2 GB swap file brings the effective
memory to 3 GB, which is plenty for testing workloads.

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Verify with `free -h` — you should see ~2 GB in the Swap row.

#### 4. Deploy the app

```bash
git clone https://github.com/YOUR_ORG/five-a-day.git
cd five-a-day
# Create .env.testing and populate it using the template in README.md (section ".env template")
touch .env.testing
# Symlink so settings.py's base load_dotenv(.env) finds the same file
ln -sf .env.testing .env
docker compose --env-file .env.testing up -d
```

Because docker-compose uses `restart: unless-stopped`, all containers come back automatically after a
VM reboot — no manual intervention needed.

**Teacher seeding (testing/production only)** — `entrypoint.sh` runs `manage.py seed_teachers` on container start when `DJANGO_ENV` is `testing` or `production`. The command reads numbered `TEACHER_SEED_<N>_*` env vars (see [README → .env template](../README.md#env-template)) and idempotently creates Teacher rows + linked `auth.User` accounts so teachers can log in with email + password.

**Enrollment-type seeding (testing/production only)** — `entrypoint.sh` also runs `manage.py seed_enrollment_types` on container start for the same environments. It provisions the `EnrollmentType` reference table (`monthly`, `quarterly`, `adults`, `special`) from `SiteConfiguration`. This is **not** optional test data: nothing else creates these rows, and without them `EnrollmentService` raises and no student can be enrolled. The command is idempotent, so it is a no-op once the rows exist.

Keep these vars directly in `.env.testing` (alongside the rest of the testing config). There is no overlay file system — `.env.testing` is self-contained and is renamed to `.env` on the VM before bringing the stack up. It's gitignored via `.env*`.

Watch the logs after `docker compose up -d` for `✅ Teacher created/updated: ...` lines confirming the seeds landed. Gmail SMTP (`EMAIL_HOST_USER` + `EMAIL_SECRET`) must work in this environment: any seed block that omits `TEACHER_SEED_<N>_PASSWORD` requires the teacher to activate via the password-reset email.

#### 5. Routine updates

**Normally you do nothing.** The `Deploy testing` workflow runs every night in the
02:00-05:00 Europe/Madrid window, compares `/health/` against `pyproject.toml` on
`origin/testing`, and deploys only when they differ — see [CI/CD — automated deploys](#4-cicd--automated-deploys). Use the
`/deploy` skill and pick **Solo testing** when you need it *now*. By hand, from your
workstation:

```bash
gcloud compute ssh fiveaday-testing --zone=us-east1-c --project=five-a-day-evolution
```

The deployed checkout lives at **`/home/Proye/five-a-day`** and is owned by the user `Proye`, which
is *not* the user `gcloud compute ssh` logs you in as. So every git call needs
`-c safe.directory=*` under `sudo`, or it fails with `detected dubious ownership`:

```bash
D=/home/Proye/five-a-day
sudo git -c safe.directory=* -C $D pull --ff-only origin testing
sudo docker system prune -f          # required — see below
sudo docker compose -f $D/docker-compose.yml -f $D/docker-compose.testing.yml up -d --build
```

> **Both `-f` files, every time.** `docker-compose.testing.yml` is not cosmetic: it overrides the
> `db` service to mount `testing_postgres_data` instead of the base file's dev volume, and replaces
> `runserver` with Gunicorn. Bringing the stack up with only the base file starts a **different,
> valid, wrong** stack — the database attaches to the dev volume (a months-old snapshot on this
> host) and nothing errors. `/health/` even reports the correct new version, because the code
> really did deploy. Confirm the mount afterwards:
>
> ```bash
> sudo docker inspect fiveaday_postgres --format '{{range .Mounts}}{{.Name}}{{println}}{{end}}'
> # must contain five-a-day_testing_postgres_data
> ```
>
> If it does not, the real data is intact in an orphaned volume. **Never run
> `docker volume prune` or `system prune --volumes` on this VM.**

> **The prune is not optional.** The e2-micro has ~969 MB RAM (~380 MB free with the stack up) plus
> 2 GB swap, and the boot disk runs around 58% of 30 GB. A `--build` here can be OOM-killed. If it
> is, retry once after `sudo docker builder prune -af`.

There is no `docker compose down` step — `up -d --build` recreates changed containers in place, and
skipping the down avoids a window where the stack is fully offline on a slow VM.

Verify the deploy landed by comparing `/health/` against `pyproject.toml` on `origin/testing`:

```bash
curl -s http://34.26.130.187:8000/health/
# {"status": "healthy", "service": "fiveaday", "version": "...", "environment": "testing"}
```

`version` must equal the `version` in `pyproject.toml` on `origin/testing`. If it does not, the
build did not actually replace the running container. Note that an `APP_VERSION` env var overrides
the value `settings.py` derives from `pyproject.toml`, and would make this check lie — neither
environment sets one today.

### Free tier limits (permanent, never expire)

| Resource          | Free allowance          | Your usage  |
|-------------------|-------------------------|-------------|
| e2-micro instance | 1 instance/month        | 1 instance  |
| Persistent disk   | 30 GB standard          | 30 GB       |
| Network egress    | 1 GB/month (to internet)| < 1 GB      |

**Cost: $0/month, permanently.**

---

## 3. Production (Cloud Run + Cloud SQL)

> **STATUS (v1.14.7):** production is **live** on Cloud Run in `europe-southwest1`, backed by
> **Cloud SQL** — <https://fiveaday-332600671945.europe-southwest1.run.app/login/>.
> An earlier plan to prototype the database on Neon before migrating to Cloud SQL was dropped;
> Cloud SQL is the only production database, and the section below describes it as deployed.

### Architecture

```
Internet
  → Cloud Run (Django/Gunicorn, 1 vCPU, 512 MB, min-0 or min-1)
      → Cloud SQL Auth Proxy → Cloud SQL (PostgreSQL 16, db-f1-micro, 10 GB)
      → Gmail SMTP (transactional email via App Password)
      → Google OAuth (admin authentication)

Cloud Scheduler (cron jobs — replaces Celery Beat)
  → Cloud Run Jobs (management commands — one job per scheduled task)

GCP Secret Manager → env vars injected into Cloud Run at startup
Artifact Registry  → Docker images built by Cloud Build
Cloud DNS          → Custom domain → Cloud Run (TLS auto-managed)
```

**Nginx is not needed.** Cloud Run handles TLS termination, load balancing, and HTTP/2 natively.
WhiteNoise serves static files directly from the Django container.

### Celery strategy on Cloud Run

Cloud Run containers must respond to HTTP — long-running Celery worker processes cannot run there.
This is solved in two layers:

**Async tasks** (email sends triggered by user actions, PDF generation):
Set `CELERY_TASK_ALWAYS_EAGER=True` in production. Tasks run synchronously inside the HTTP request.
Imperceptible for 4 teachers and occasional sends. No Redis or worker process needed.

**Periodic tasks** (Celery Beat — birthday emails, payment reminders, scheduled reports):
Use **Cloud Scheduler** to trigger **Cloud Run Jobs** that execute Django management commands.
Each Beat schedule becomes one Scheduler job. Setup is covered in the [Celery Beat section](#celery-beat--cloud-scheduler) below.

### Prerequisites

1. GCP project with billing enabled
2. `gcloud` CLI installed and authenticated (`gcloud auth login`)
3. Docker installed locally
4. Gmail account with an App Password for SMTP
5. Google OAuth credentials (Client ID + Secret) from Google Cloud Console

### Initial setup

#### 1. Enable required APIs

```bash
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  cloudscheduler.googleapis.com \
  dns.googleapis.com
```

#### 2. Create Cloud SQL instance

```bash
gcloud sql instances create fiveaday-db \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --region=europe-southwest1 \
  --storage-size=10GB \
  --storage-auto-increase \
  --backup-start-time=03:00 \
  --availability-type=zonal

gcloud sql databases create fiveaday_db --instance=fiveaday-db
gcloud sql users create fiveaday_user \
  --instance=fiveaday-db \
  --password=YOUR_SECURE_PASSWORD
```

**Why 10 GB**: With HistoryLog capped at 1,000 rows, the database grows slowly. Payment history for
1,000 students over 20 years (~360,000 rows) plus all other tables totals under 1 GB. 10 GB provides
a decade of headroom with auto-increase as a safety net.

#### 3. Create Artifact Registry repository

```bash
gcloud artifacts repositories create fiveaday \
  --repository-format=docker \
  --location=europe-southwest1
```

#### 4. Store secrets in Secret Manager

```bash
echo -n "your-django-secret-key"    | gcloud secrets create DJANGO_SECRET_KEY    --data-file=-
echo -n "your-db-password"          | gcloud secrets create POSTGRES_PASSWORD     --data-file=-
echo -n "your-gmail@gmail.com"      | gcloud secrets create EMAIL_HOST_USER       --data-file=-
echo -n "your-gmail-app-password"   | gcloud secrets create EMAIL_SECRET          --data-file=-
echo -n "your-google-client-id"     | gcloud secrets create GOOGLE_CLIENT_ID      --data-file=-
echo -n "your-google-client-secret" | gcloud secrets create GOOGLE_CLIENT_SECRET  --data-file=-

# Teacher seeds — one block per teacher who should be able to log in.
# Anything not matching ^TEACHER_SEED_<N>_(FIRST_NAME|LAST_NAME|EMAIL|PHONE|ADMIN|PASSWORD)$ is ignored.
# Omit ..._PASSWORD to make the teacher activate via /password-reset/ instead of receiving an initial password.
echo -n "Joaquin"                   | gcloud secrets create TEACHER_SEED_1_FIRST_NAME --data-file=-
echo -n "Hernandez"                 | gcloud secrets create TEACHER_SEED_1_LAST_NAME  --data-file=-
echo -n "owner@example.com"         | gcloud secrets create TEACHER_SEED_1_EMAIL      --data-file=-
echo -n "True"                      | gcloud secrets create TEACHER_SEED_1_ADMIN      --data-file=-
echo -n "your-initial-password"     | gcloud secrets create TEACHER_SEED_1_PASSWORD   --data-file=-
```

**`HEALTH_PROBE_TOKEN` (v1.16).** **Configured** as of 2026-08-31: a Secret Manager secret with
a per-secret `secretAccessor` binding for `fiveaday-run`, attached to the service as a secret
ref (env entries 35 → 36, secret refs 6 → 7), and mirrored to the `HEALTH_PROBE_TOKEN` GitHub
secret so `Deploy production` can use it. It unlocks the
row-count fingerprint on `/health/?deep=1`, which is what lets a deploy prove the release did
not land on the wrong database. Without it the deep probe still reports connectivity and
migration state, just not the counts:

```bash
PROJECT=five-a-day-evolution
REGION=europe-southwest1
RUN_SA=fiveaday-run@five-a-day-evolution.iam.gserviceaccount.com

openssl rand -hex 32 | gcloud secrets create HEALTH_PROBE_TOKEN \
  --data-file=- --replication-policy=automatic --project=$PROJECT

# REQUIRED. The runtime service account holds only roles/cloudsql.client and
# roles/logging.logWriter at the project level — it has NO project-wide
# secretAccessor. Every existing secret (DATABASE_URL, DJANGO_SECRET_KEY, …)
# carries its own per-secret binding, so a new secret without one cannot be
# read: the revision fails to become ready and traffic silently stays on the
# old one. Skipping this step is the failure mode, not an optimisation.
gcloud secrets add-iam-policy-binding HEALTH_PROBE_TOKEN \
  --member=serviceAccount:$RUN_SA \
  --role=roles/secretmanager.secretAccessor --project=$PROJECT

# --update-secrets is ADDITIVE, unlike --set-env-vars, so it will not drop the
# existing env set. Verify the variable count before and after regardless
# (expect 35 vars / 6 secret refs before, 36 / 7 after).
gcloud run services update fiveaday --region=$REGION --project=$PROJECT \
  --update-secrets=HEALTH_PROBE_TOKEN=HEALTH_PROBE_TOKEN:latest
```

Confirm it took effect — the deep probe gains a `database.counts` object:

```bash
TOKEN=$(gcloud secrets versions access latest --secret=HEALTH_PROBE_TOKEN --project=$PROJECT)
curl -s -H "X-Probe-Token: $TOKEN" \
  "https://fiveaday-332600671945.europe-southwest1.run.app/health/?deep=1"
```

**Cloud Run startup + liveness probes on `/health/`.** Without these the service runs on the
implicit default probe only — a TCP check on port 8000 and **no liveness probe at all** — so a
container that binds the port but can no longer serve HTTP is never restarted, and startup is
"ready" the moment Gunicorn binds rather than when Django actually answers. Both probes target
the **shallow** `/health/` (never `?deep=1`): it deliberately skips the database, so a transient
DB blip cannot make Cloud Run kill healthy containers. The startup budget must stay at the
240 s maximum because a cold start runs `migrate` + `collectstatic --clear` before Gunicorn
binds (`10 + 23×10 = 240`).

Three traps, all hit on the first attempt (2026-09-01, revision `fiveaday-00016-5f5`):

- **Probe requests arrive with `Host: 127.0.0.1`** — Cloud Run probes hit the container
  directly, bypassing the ingress that normally sets the real host, so Django's strict
  `ALLOWED_HOSTS` answers 400 (`DisallowedHost`) and the revision never becomes ready.
  `DJANGO_ALLOWED_HOSTS` must include `127.0.0.1` (harmless: a poisoned link pointing at
  loopback gives an attacker nothing, and the production guard only forbids `*`).
- **The probe "success" is a 301, not a 200** — probe traffic also lacks
  `X-Forwarded-Proto: https`, so `SECURE_SSL_REDIRECT` answers with a redirect. Cloud Run
  counts any status in 200–399 as success, so this works, but know that the probe proves
  "Gunicorn + Django middleware answering", not "view layer executed".
- **PowerShell eats the unquoted commas** — the flag value collapses into one garbled path
  (`GET /health/ httpGet.port=8000 …`). Quote each flag value, or run the block in Git Bash.

```powershell
# Belt-and-braces: the env set has been 100 % ASCII since 2026-09-01 (see "Repairing a
# single env var"), but ANY `gcloud run services update` re-serialises the whole set
# client-side, so keep the UTF-8 vars set in case a non-ASCII value ever returns:
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"

# One revision for env var + both probes. ^@^ makes @ the delimiter so the commas in
# the host list survive; --update-env-vars merges (never --set-env-vars, which replaces).
gcloud run services update fiveaday `
  --region=europe-southwest1 --project=five-a-day-evolution `
  --update-env-vars '^@^DJANGO_ALLOWED_HOSTS=fiveaday-332600671945.europe-southwest1.run.app,fiveaday-rsw37gr6lq-no.a.run.app,127.0.0.1' `
  --startup-probe 'httpGet.path=/health/,httpGet.port=8000,initialDelaySeconds=10,periodSeconds=10,timeoutSeconds=5,failureThreshold=23' `
  --liveness-probe 'httpGet.path=/health/,httpGet.port=8000,periodSeconds=30,timeoutSeconds=5,failureThreshold=3'

# Verify the probes took…
gcloud run services describe fiveaday --region=europe-southwest1 --project=five-a-day-evolution `
  --format="yaml(spec.template.spec.containers[0].startupProbe, spec.template.spec.containers[0].livenessProbe)"

# …and that ACADEMY_IBAN_HOLDER survived — since 2026-09-01 the correct value is the
# deliberately ASCII "Silvia Yubitza Moreno Carlin" (plain i): any `?` means corruption
gcloud run services describe fiveaday --region=europe-southwest1 --project=five-a-day-evolution `
  --format="value(spec.template.spec.containers[0].env)" `
  | python -c "import sys; s=sys.stdin.read(); print('IBAN holder OK' if 'Silvia Yubitza Moreno Carlin' in s and 'Carl?n' not in s else 'CORRUPTED')"
```

This creates a new revision and shifts traffic to it, so treat it like a small deploy. A
failed probe config is safe for traffic — Cloud Run keeps serving the previous revision —
but **the service template still records the failed attempt**, including any env-var
corruption it carried: the 2026-09-01 probe rollout ran once without the UTF-8 exports,
failed on `DisallowedHost`, and left `ACADEMY_IBAN_HOLDER` as `Carl?n` in the template,
which the corrected retry then faithfully preserved — and a further repair attempt showed
the corruption can also happen **before gcloud runs at all**, when the console/clipboard
mangles the `í` on paste, which no `PYTHONUTF8` export can undo. After ANY update —
successful or not — run the check above and repair per ".claude/skills/deploy/SKILL.md →
Repairing a single env var" if it reports corruption.

**Resolution (2026-09-01): `ACADEMY_IBAN_HOLDER` is now deliberately ASCII —
`Silvia Yubitza Moreno Carlin`, plain `i`.** This is an accepted spelling tradeoff on the
payment-reminder emails in exchange for making the value un-corruptible: with no non-ASCII
byte in the env set, any future `--update-env-vars` from any shell is safe. Do NOT "repair"
it back to `Carlín` — that reintroduces the trap. If a future env var genuinely needs
non-ASCII, set it from Cloud Shell or Git Bash (never a PowerShell/cmd console) and run the
byte check afterwards.

### Build & Deploy

```bash
PROJECT_ID=five-a-day-evolution   # no default project is set in gcloud config — do not derive it
export CLOUDSDK_CORE_PROJECT=$PROJECT_ID   # makes every gcloud command below target it
REGION=europe-southwest1
IMAGE=$REGION-docker.pkg.dev/$PROJECT_ID/fiveaday/web

# Build and push image
gcloud builds submit --tag $IMAGE .

# Deploy
gcloud run deploy fiveaday \
  --image=$IMAGE \
  --platform=managed \
  --region=$REGION \
  --port=8000 \
  --min-instances=0 \
  --max-instances=2 \
  --memory=512Mi \
  --cpu=1 \
  --timeout=120 \
  --allow-unauthenticated \
  --add-cloudsql-instances=$PROJECT_ID:$REGION:fiveaday-db \
  --set-env-vars="DJANGO_ENV=production" \
  --set-env-vars="DJANGO_DEBUG=False" \
  --set-env-vars="DJANGO_ALLOWED_HOSTS=fiveaday-332600671945.europe-southwest1.run.app" \
  --set-env-vars="DATABASE_URL=postgres://fiveaday_user:PASSWORD@/fiveaday_db?host=/cloudsql/$PROJECT_ID:$REGION:fiveaday-db" \
  --set-env-vars="CELERY_TASK_ALWAYS_EAGER=True" \
  --set-env-vars="GOOGLE_REDIRECT_URI=https://fiveaday-332600671945.europe-southwest1.run.app/auth/google/callback/" \
  --set-env-vars="TEACHER_SEED_1_ADMIN=True" \
  --set-secrets="DJANGO_SECRET_KEY=DJANGO_SECRET_KEY:latest" \
  --set-secrets="EMAIL_HOST_USER=EMAIL_HOST_USER:latest" \
  --set-secrets="EMAIL_SECRET=EMAIL_SECRET:latest" \
  --set-secrets="GOOGLE_CLIENT_ID=GOOGLE_CLIENT_ID:latest" \
  --set-secrets="GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest" \
  --set-secrets="TEACHER_SEED_1_FIRST_NAME=TEACHER_SEED_1_FIRST_NAME:latest" \
  --set-secrets="TEACHER_SEED_1_LAST_NAME=TEACHER_SEED_1_LAST_NAME:latest" \
  --set-secrets="TEACHER_SEED_1_EMAIL=TEACHER_SEED_1_EMAIL:latest" \
  --set-secrets="TEACHER_SEED_1_PASSWORD=TEACHER_SEED_1_PASSWORD:latest"
```

**Optional env vars / secrets** — omit any feature you are not using; each one is dormant and
harmless when unset. Add them to the same `gcloud run deploy` invocation:

```bash
  # Support + QA error mail
  --set-secrets="SUPPORT_EMAIL=SUPPORT_EMAIL:latest" \
  # Restrict Google login to a single address (defaults to EMAIL_HOST_USER)
  --set-secrets="GOOGLE_ALLOWED_EMAIL=GOOGLE_ALLOWED_EMAIL:latest" \
  # Prefilled into the payment-reminder email forms. NOTE: on the live production
  # service these three are currently PLAIN env vars, set by hand, not Secret Manager
  # refs. See "Repairing a single env var" below before changing them.
  --set-secrets="ACADEMY_IBAN=ACADEMY_IBAN:latest" \
  --set-secrets="ACADEMY_IBAN_HOLDER=ACADEMY_IBAN_HOLDER:latest" \
  --set-secrets="ACADEMY_PHONE=ACADEMY_PHONE:latest" \
  # Stripe (v1.11) — STRIPE_WEBHOOK_SECRET is REQUIRED if STRIPE_SECRET_KEY is set,
  # otherwise the webhook skips signature verification entirely
  --set-secrets="STRIPE_SECRET_KEY=STRIPE_SECRET_KEY:latest" \
  --set-secrets="STRIPE_PUBLISHABLE_KEY=STRIPE_PUBLISHABLE_KEY:latest" \
  --set-secrets="STRIPE_WEBHOOK_SECRET=STRIPE_WEBHOOK_SECRET:latest" \
  # Twilio SMS (v1.8) — opt-in parents only
  --set-secrets="TWILIO_ACCOUNT_SID=TWILIO_ACCOUNT_SID:latest" \
  --set-secrets="TWILIO_AUTH_TOKEN=TWILIO_AUTH_TOKEN:latest" \
  --set-secrets="TWILIO_FROM_NUMBER=TWILIO_FROM_NUMBER:latest" \
  # Google Sheets export (v1.2) — inline JSON is the Secret Manager-friendly form
  --set-secrets="GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON=GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON:latest" \
  --set-env-vars="GOOGLE_SHEETS_SPREADSHEET_ID=<doc-id>" \
  # Needed only behind a proxy or a custom domain
  --set-env-vars="CSRF_TRUSTED_ORIGINS=https://fiveaday-332600671945.europe-southwest1.run.app" \
  # Cloud Run puts exactly one proxy in front of the app. The rate limiter reads the
  # client IP this many hops from the RIGHT of X-Forwarded-For, because a proxy APPENDS
  # what it saw — anything further left is client-supplied and therefore spoofable.
  --set-env-vars="TRUSTED_PROXY_COUNT=1" \
  --set-env-vars="LOG_LEVEL=INFO"
```

Additional teachers are numbered blocks — `TEACHER_SEED_2_*`, `TEACHER_SEED_3_*`, and so on.
Iteration stops at the first missing `FIRST_NAME`, so the numbers must not skip.

> `LOGIN_USERNAME` / `LOGIN_PASSWORD` are dev-only basic-auth credentials. In testing and production, login goes through `auth.User` (Teacher email + hashed password) — set Teachers up via `TEACHER_SEED_*` instead. `SimpleAuthMiddleware` is still in the stack: it enforces session auth in every environment and adds a non-admin Teacher whitelist in testing/production.

After the first deploy, note the Cloud Run URL (production is `https://fiveaday-332600671945.europe-southwest1.run.app`) and
update:
- `DJANGO_ALLOWED_HOSTS` with the actual URL
- `GOOGLE_REDIRECT_URI` with `https://YOUR_URL/auth/google/callback/`
- Google Cloud Console → OAuth credentials → Authorized redirect URIs (add the callback URL)

### Repairing a single env var

> **RESOLVED 2026-09-01: the env set is now 100 % ASCII — `ACADEMY_IBAN_HOLDER` is
> deliberately `Silvia Yubitza Moreno Carlin` (plain `i`) on the service and in all four
> local `.env*` files. Do NOT "repair" it back to `Carlín`.** The history below explains
> why, and still applies to any non-ASCII value you might add in the future:
> `--update-env-vars` is a client-side merge — gcloud reads the entire current env set,
> splices in your change and PUTs all of it back. So an update to *any* variable
> re-serialises every other one, and on a Windows shell with a non-UTF-8 codepage a
> non-ASCII value silently corrupts (`í` → `?`). Verified the hard way twice: repaired in
> v1.23.0, re-corrupted by an unrelated `--update-env-vars=CACHE_DB=True`; repaired again
> 2026-09-01, re-corrupted by a console-paste mangle that happened *before gcloud ran*,
> which no `PYTHONUTF8` export can prevent — hence the ASCII decision. If you ever add a
> non-ASCII value, read the bytes back after every update (for `í` the hex must contain
> `c3 ad`, never `3f`):
>
> ```bash
> gcloud run services describe fiveaday --project=$PROJECT --region=$REGION --format=json >   | python -c "import json,sys; e={x['name']:x.get('value') for x in json.load(sys.stdin)['spec']['template']['spec']['containers'][0]['env']}; print(e['ACADEMY_IBAN_HOLDER'].encode('utf-8').hex(' '))"
> ```


Nothing in the repo provisions the `ACADEMY_*` values — they were set by hand, so nothing
re-asserts them either and a bad value survives every future deploy.

Use **`--update-env-vars`**, which *merges*. `--set-env-vars` **replaces the whole set** and would
silently drop the ~30 vars and 6 Secret Manager refs you did not repeat on the line:

```bash
gcloud run services update fiveaday --project=$PROJECT_ID --region=$REGION \
  --update-env-vars="^@^SOME_VAR=some value, with commas"
```

The `^@^` prefix makes `@` the delimiter, so spaces and commas in the value are safe. A non-ASCII
value additionally needs a **UTF-8 shell**: passing `í` from a legacy-codepage cmd/PowerShell
prompt transcodes it through the console codepage and gcloud stores a literal `?` — that is how
`ACADEMY_IBAN_HOLDER` was corrupted twice before the 2026-09-01 decision to keep it ASCII. Today
no env value carries a non-ASCII byte; check any new one you add. (The GDPR legal footer in
`base_email.html` keeps the accented legal name `Carlín` — it is file-based UTF-8 rendered by
Django and cannot transcode.)

Snapshot the env before and after (the count must not move — expect 35 vars and 6 `secretKeyRef`
entries) and verify through a request path that prints the value; `/apps/payment-reminder/` renders
`{{ iban_holder }}` in its preview. The full procedure, including the snapshot command, is in
`.claude/skills/deploy/SKILL.md`.

This rolls a new revision with zero downtime.

### Run migrations

Required on first deploy and after any model change. The v1.0.12 release introduced `students.0003_teacher_user` (adds `Teacher.user` OneToOneField, nullable so existing rows survive); subsequent deploys need a migrate run only when new migrations land.

```bash
gcloud run jobs create fiveaday-migrate \
  --image=$IMAGE \
  --region=$REGION \
  --add-cloudsql-instances=$PROJECT_ID:$REGION:fiveaday-db \
  --set-env-vars="DATABASE_URL=postgres://fiveaday_user:PASSWORD@/fiveaday_db?host=/cloudsql/$PROJECT_ID:$REGION:fiveaday-db" \
  --command="python" \
  --args="project/manage.py,migrate"

gcloud run jobs execute fiveaday-migrate --region=$REGION --wait
```

On subsequent deploys with model changes, **update the job's image first, then run it**:

```bash
gcloud run jobs update fiveaday-migrate --image=$IMAGE --region=$REGION
gcloud run jobs execute fiveaday-migrate --region=$REGION --wait
```

> **Cloud Run Jobs pin their own image — this is the easiest way to break a deploy.** Every job
> stores the image tag it was created or last updated with; deploying a new image to the *service*
> does not touch them. Executing `fiveaday-migrate` without the `update` above runs the **previous
> release's** migration set against production, and leaves all seven scheduled jobs executing old
> code indefinitely. See [Routine deploys](#routine-deploys) for the full loop over all the jobs.

> **v1.26.1 — `billing/0010` can legitimately REFUSE to apply.** It adds
> `unique_pending_periodic_payment_per_month`, and it pre-checks the table first: if production
> already holds more than one **pending** monthly/quarterly payment for the same student and due
> month, the migration aborts with a `CommandError` naming the offending `student_id`s instead of
> failing with a bare Postgres constraint violation half-way through. That is not a bug in the
> migration — those rows are double-billed. Repair them and re-run:
>
> ```bash
> # Inspect first — dry run is the default and writes nothing.
> gcloud run jobs update fiveaday-reconcile --image=$IMAGE --region=$REGION   # if the job exists
> # ...or from a shell against the same database:
> python manage.py reconcile_payment_schedule
> python manage.py reconcile_payment_schedule --apply --cancel-stale
> ```
>
> The migration also adds an index to `payments` and one to `fun_friday_attendance`. Both are
> single statements and are subject to the new `statement_timeout` (30 s by default) — at this
> academy's row counts an index build is sub-second, but set `DB_STATEMENT_TIMEOUT_MS` higher for
> the run if a future migration rewrites a large table.

### Celery Beat → Cloud Scheduler

Each Celery Beat periodic task becomes a Cloud Scheduler job that triggers a Cloud Run Job running a
Django management command. Every Beat task has a command wrapper (they run the task synchronously
via `.apply()` — Cloud Run Jobs need `CELERY_TASK_ALWAYS_EAGER=True` so nested `.delay()` calls also
run inline):

| Beat task | Management command | Schedule (Europe/Madrid) | Cron |
|---|---|---|---|
| `generate_monthly_payments_task` | `generate_payments` | 1st of month, 06:00 | `0 6 1 * *` |
| `materialize_recurring_expenses_daily_task` | `materialize_recurring_expenses --daily` | daily, 06:15 | `15 6 * * *` |
| `materialize_recurring_expenses_task` | `materialize_recurring_expenses` | 1st of month, 06:30 | `30 6 1 * *` |
| `send_birthday_emails_task` | `send_birthday_emails` | daily, 08:00 | `0 8 * * *` |
| `send_payment_reminders` | `send_payment_reminders` | Mondays, 09:00 | `0 9 * * 1` |
| `send_due_fun_friday_emails_task` | `send_due_fun_friday_emails` | daily, 14:30 | `30 14 * * *` |
| `send_monthly_report_task` | `send_monthly_report` | 28th of month, 20:00 | `0 20 28 * *` |
| `cleanup_done_backlog_tasks` | `cleanup_backlog_tasks` | QA/testing env only — skip in production | — |
| `prune_audit_log` | `prune_audit_log` | weekly, Sunday 03:00 | `0 3 * * 0` |
| `purge_expired_sessions` | `purge_sessions` | daily, 03:30 | `30 3 * * *` |
| — (ops only, no Beat task) | `backup_retention --apply` | daily, 05:30 | `30 5 * * *` |

> **Provisioning status (verified 2026-09-01).** 11 Cloud Run Jobs, 9 Cloud Scheduler
> entries. `fiveaday-migrate` has no schedule by design (deploy-time only). Note the
> **schedulers live in `europe-west1`**, not the service's `europe-southwest1` — Cloud
> Scheduler is not available in that region, so `gcloud scheduler jobs list --location`
> must say `europe-west1` or it silently returns nothing.
>
> `fiveaday-prune-audit-log` was **missing entirely** until v1.23.0: this table listed it
> from v1.15 but no job and no schedule were ever created, so the audit trail was never
> actually pruned in production. Created and smoke-tested.
>
> `sched-fiveaday-purge-sessions` is **PAUSED** until v1.23.0 is deployed — the
> `purge_sessions` command does not exist in the currently-deployed image, so an enabled
> schedule would just fail nightly. **Resume it immediately after the v1.23.0 production
> deploy:**
>
> ```bash
> gcloud scheduler jobs resume sched-fiveaday-purge-sessions >   --project=five-a-day-evolution --location=europe-west1
> ```
>
> `fiveaday-backup-retention` (v1.26.0) is the **scheduled** port of
> `scripts/backup_retention.sh` — `manage.py backup_retention --apply`, daily 05:30. The
> Cloud Run Job exists (cloned from `fiveaday-prune-audit-log`, SQL annotation intact) and
> needs two one-off steps by a human with IAM rights:
>
> ```bash
> # 1. grant the custom role (created 2026-09-01: backupRuns.create/delete/get/list +
> #    instances.get — deliberately NOT instances.update) to the runtime SA:
> gcloud projects add-iam-policy-binding five-a-day-evolution >   --member="serviceAccount:fiveaday-run@five-a-day-evolution.iam.gserviceaccount.com" >   --role="projects/five-a-day-evolution/roles/backupRetention" --condition=None
>
> # 2. create the schedule PAUSED (the deployed image predates the command):
> gcloud scheduler jobs create http sched-fiveaday-backup-retention >   --location=europe-west1 --schedule="30 5 * * *" --time-zone="Europe/Madrid" >   --uri="https://europe-southwest1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/five-a-day-evolution/jobs/fiveaday-backup-retention:run" >   --http-method=POST >   --oauth-service-account-email="fiveaday-scheduler@five-a-day-evolution.iam.gserviceaccount.com" >   --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
> gcloud scheduler jobs pause sched-fiveaday-backup-retention --location=europe-west1
> ```
>
> **Resume it right after the v1.26.0 production deploy** (and run once with
> `--bootstrap` via `gcloud run jobs execute fiveaday-backup-retention --args` if the
> tiers have never been seeded): like `purge_sessions` above, an enabled schedule against
> the current image would just fail nightly.
>
> **Cloning a job? Use `gcloud run jobs replace` with a YAML file, not a pile of flags.**
> Export an existing job with `--format=export`, change `metadata.name` and the container
> `args`, drop the creator/timestamp annotations, and replace. It reproduces all ~30 env
> vars and 4 secret refs exactly, and because the value travels in a UTF-8 file it cannot
> be mangled by the console codepage the way `--update-env-vars` can.
>
> **`purge_sessions` is security housekeeping, not cosmetics (v1.23.0).** `django_session`
> is the default database session backend and its payloads are base64 — signed, not
> encrypted — so anything a view puts in a session is readable by anyone who can read the
> table, and rows outlived their cookies indefinitely. `parent_session_tokens` likewise kept
> every magic link ever issued. Neither was purged before this release. Schedule it in
> production like the others; skipping it is not a no-op.

> **Fun Friday announcements** are NOT sent with `apply_async(eta=...)` (the ETA is silently
> ignored in eager mode, which would send immediately). The form persists a
> `FunFridayScheduledSend` row scheduled for Monday 14:30 of the event week; the
> `send_due_fun_friday_emails` job drains due rows and marks them sent (idempotent). If the
> Monday slot already passed when the event is created, the app drains immediately on its own.

#### Step 1 — Create a reusable Cloud Run Job per task

```bash
# Example: generate monthly payments
gcloud run jobs create fiveaday-generate-payments \
  --image=$IMAGE \
  --region=$REGION \
  --add-cloudsql-instances=$PROJECT_ID:$REGION:fiveaday-db \
  --set-env-vars="DATABASE_URL=...,DJANGO_ENV=production,DJANGO_DEBUG=False,CELERY_TASK_ALWAYS_EAGER=True" \
  --command="python" \
  --args="project/manage.py,generate_payments"

# Example: send birthday emails (email-sending jobs also need the email secrets)
gcloud run jobs create fiveaday-birthday-emails \
  --image=$IMAGE \
  --region=$REGION \
  --add-cloudsql-instances=$PROJECT_ID:$REGION:fiveaday-db \
  --set-env-vars="DATABASE_URL=...,DJANGO_ENV=production,DJANGO_DEBUG=False,CELERY_TASK_ALWAYS_EAGER=True" \
  --set-secrets="EMAIL_HOST_USER=EMAIL_HOST_USER:latest,EMAIL_SECRET=EMAIL_SECRET:latest" \
  --command="python" \
  --args="project/manage.py,send_birthday_emails"

# Commands with flags pass them as extra args, e.g. the daily expense materializer:
#   --args="project/manage.py,materialize_recurring_expenses,--daily"
```

Repeat for each row of the table. Reuse the same `--image` and `--add-cloudsql-instances` flags.

#### Step 2 — Schedule each job with Cloud Scheduler

Cloud Scheduler triggers the Cloud Run Jobs API to execute a job on a cron schedule.

```bash
# Get the service account Cloud Run uses (or create a dedicated one)
SA=fiveaday-scheduler@$PROJECT_ID.iam.gserviceaccount.com

gcloud iam service-accounts create fiveaday-scheduler \
  --display-name="Five a Day Scheduler"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA" \
  --role="roles/run.invoker"

# Schedule: generate payments on the 1st of every month at 08:00 Madrid time
gcloud scheduler jobs create http fiveaday-generate-payments \
  --location=$REGION \
  --schedule="0 8 1 * *" \
  --time-zone="Europe/Madrid" \
  --uri="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/fiveaday-generate-payments:run" \
  --message-body="{}" \
  --oauth-service-account-email=$SA

# Schedule: birthday emails daily at 08:00 (matches the Beat schedule table above)
gcloud scheduler jobs create http fiveaday-birthday-emails \
  --location=$REGION \
  --schedule="0 8 * * *" \
  --time-zone="Europe/Madrid" \
  --uri="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/fiveaday-birthday-emails:run" \
  --message-body="{}" \
  --oauth-service-account-email=$SA
```

Repeat for each periodic task. The first 3 Scheduler jobs per month are free; beyond that it is
$0.10/job/month — 20 jobs costs **$1.70/month**.

### Custom domain

```bash
gcloud run domain-mappings create \
  --service=fiveaday \
  --domain=app.yourdomain.com \
  --region=$REGION
```

The command outputs DNS records to add at your domain registrar (a CNAME or A record). Cloud Run
issues and renews the TLS certificate automatically. Then update `DJANGO_ALLOWED_HOSTS` to include
the custom domain and redeploy.

**Domain options:**

- External registrar (Namecheap, Porkbun): ~€10-15/year for `.com` or `.es`, point a CNAME to the
  Cloud Run URL.
- Cloud Domains (in GCP): ~$12/year, DNS managed automatically within GCP.

### Routine deploys

The `/deploy` skill (`.claude/skills/deploy/SKILL.md`) automates everything below, including the
branch guard, the Cloud SQL backup and the post-deploy version check. Prefer it over running these
by hand. It always asks first which target to deploy — testing only, production only, or both —
so production is never reached without an explicit choice.

Order matters: **migrate before the service rolls**, not after. Rolling the service first puts new
code in front of an old schema for the length of the rollout.

```bash
# Image tags are the git short SHA of the deployed commit
IMAGE=$REGION-docker.pkg.dev/$PROJECT_ID/fiveaday/web:$(git rev-parse --short origin/main)

# 1. Back up before touching production data
gcloud sql backups create --instance=fiveaday-db

# 2. Build new image
gcloud builds submit --tag $IMAGE .

# 3. Repoint ALL Cloud Run Jobs at the new image (11 as of v1.26.0) — they each pin their own tag
for JOB in $(gcloud run jobs list --region=$REGION --format='value(metadata.name)'); do
  gcloud run jobs update $JOB --image=$IMAGE --region=$REGION
done

# 4. Run migrations if any models changed (skip when there are none)
gcloud run jobs execute fiveaday-migrate --region=$REGION --wait

# 5. Deploy (Cloud Run performs a rolling update with zero downtime).
#    Pass --image ONLY: re-specifying --set-env-vars replaces the whole set and silently
#    drops the ~30 env vars and 6 Secret Manager refs not repeated on the command line.
gcloud run deploy fiveaday --image=$IMAGE --region=$REGION

# 6. Confirm the deploy actually landed — compare against pyproject.toml on origin/main
curl -s https://fiveaday-332600671945.europe-southwest1.run.app/health/
```

Because image tags are git SHAs, you can check whether a deploy has pending migrations without
running anything:

```bash
OLD_SHA=$(gcloud run services describe fiveaday --region=$REGION \
  --format='value(spec.template.spec.containers[0].image)' | sed 's/.*://')
git diff --name-only $OLD_SHA..$(git rev-parse --short origin/main) -- '*/migrations/*.py'
```

Rollback: prefer the **Rollback production** workflow (Actions tab), which repoints the
service AND all Cloud Run jobs at a previous image and verifies `/health/` afterwards. By
hand, redeploy the previous image tag rather than shifting traffic to a named revision:

```bash
gcloud run revisions list --service=fiveaday --region=$REGION
gcloud run deploy fiveaday --image=$IMAGE_BASE:<PREVIOUS_SHA> --region=$REGION
# and repoint every job (gcloud run jobs list … | gcloud run jobs update … --image=…)
```

> `gcloud run services update-traffic --to-revisions=<X>=100` also works, but it PINS
> traffic to that revision: every later `gcloud run deploy` then lands with **0 % traffic**
> and its verify step fails while production silently keeps serving the pinned revision.
> If you ever use it, undo the pin afterwards with `--to-latest`.

### One-off: reconciling the billing schedule (v1.22.0)

v1.22.0 anchors quarterly blocks to the month a student enrolled instead of a fixed
Oct/Jan/Apr calendar, and prorates the first period by join date. **There is no
database migration** — the models did not change, only the logic that decides which
payments exist. Rows already in the database therefore still carry the old shape,
and they will not repair themselves: the generator's idempotency check matches on
due month/year, and the new due dates differ, so simply running `generate_payments`
would create a **second, overlapping set of payments**.

Run `reconcile_payment_schedule` once per environment after deploying. It is a dry
run unless `--apply` is passed, it never modifies a payment that was already
collected, and it cancels superseded rows rather than deleting them.

**Always read the dry run first.** On testing:

```bash
# On the VM, inside the two-file compose stack
docker compose -f docker-compose.yml -f docker-compose.testing.yml exec -w /app/project web python manage.py reconcile_payment_schedule

# Happy with the plan? Apply it.
docker compose -f docker-compose.yml -f docker-compose.testing.yml exec -w /app/project web python manage.py reconcile_payment_schedule --apply --cancel-stale
```

**Production currently has no students**, so the dry run there should report all
zeros. That makes it a useful sanity check rather than a repair: a non-zero count
means production is not in the state you expect — most likely you are pointed at
the wrong database (see the v1.16.0 testing-VM incident above, where a stale
volume served a months-old dataset while `/health/` still reported the right
version). Investigate before passing `--apply`.

Run it via the ad-hoc job (take an on-demand backup first — `--apply` writes to
the payments table):

```bash
gcloud sql backups create --instance=fiveaday-db --description="pre-v1.22.0-schedule-reconcile"

gcloud run jobs update fiveaday-cmd --image=$IMAGE --region=$REGION --args="project/manage.py,reconcile_payment_schedule"
gcloud run jobs execute fiveaday-cmd --region=$REGION --wait
gcloud run jobs logs read fiveaday-cmd --region=$REGION   # READ THIS BEFORE APPLYING

gcloud run jobs update fiveaday-cmd --image=$IMAGE --region=$REGION --args="project/manage.py,reconcile_payment_schedule,--apply,--cancel-stale"
gcloud run jobs execute fiveaday-cmd --region=$REGION --wait
```

What the output means:

| Line | Meaning |
| ---- | ------- |
| `+ <student>: <concept> due <date>` | A period the new schedule wants that has no payment. Created with `--apply`. |
| `- <student>: cancelling <concept>` | A **pending** row whose due date matches no period. Cancelled (not deleted) with `--apply --cancel-stale`. |
| `REVIEW <student>` | The enrollment has **collected** payments, so it is left untouched. Its gaps and stale rows are listed for a human to settle by hand. |
| `N already correct` | Nothing to do — safe to re-run any time. |

`REVIEW` rows are the ones that need judgement: money has changed hands against the
old schedule, so the fix is a business decision (issue the missing month? absorb it?)
rather than something a script should guess. `--force` reconciles them anyway and
should only be used when you have decided that is right.

Re-running is safe and idempotent — a second pass reports `0 payment(s) to create`.

### Monitoring & maintenance

```bash
# Tail live logs
gcloud run services logs tail fiveaday --region=$REGION

# Search recent logs
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=fiveaday" \
  --limit=50

# Manual database backup
gcloud sql backups create --instance=fiveaday-db

# List backups
gcloud sql backups list --instance=fiveaday-db

# Restore from a backup
gcloud sql backups restore BACKUP_ID --restore-instance=fiveaday-db

# Run any Django management command on production data
gcloud run jobs create fiveaday-cmd \
  --image=$IMAGE \
  --region=$REGION \
  --add-cloudsql-instances=$PROJECT_ID:$REGION:fiveaday-db \
  --set-env-vars="DATABASE_URL=..." \
  --command="python" \
  --args="project/manage.py,YOUR_COMMAND,--arg1,value1"

gcloud run jobs execute fiveaday-cmd --region=$REGION --wait
```

---

## Backups and Recovery

### What protects production

| | |
|---|---|
| Automated backups | nightly **23:00 UTC**, retention **7** (a flat count, not an age) |
| Tier: biweekly | 1 on-demand tagged `tier:biweekly`, created on the 1st and the 16th |
| Tier: monthly | 1 on-demand tagged `tier:monthly`, created on the last day of the month |
| Manual / deploy | on-demand, the **3 most recent** are kept |
| Point-in-time recovery | enabled, **7 days** of transaction logs in Cloud Storage |
| Stored in | `eu` **multi-region** — outside the instance's zone |
| Instance | `europe-southwest1-b`, **ZONAL** (no HA replica, no automatic failover) |
| Deletion protection | **enabled** |

Backups live in Google-managed storage in the `eu` multi-region, not in a bucket you own —
the project has no GCS buckets, and they are only reachable through the
`gcloud sql backups` API. Because that location is multi-region, a failure of the instance's
zone takes production offline but does **not** lose the backups.

Deletion protection matters more than it looks: **deleting a Cloud SQL instance deletes every
backup it owns**. That is the single fastest route to total data loss, and it is blocked.

### Why retention needs a script

Cloud SQL's retention is a flat count of **automated** backups — there is no
grandfather-father-son option — so on its own the recovery horizon is one week. **On-demand**
backups are exempt from that count and persist until explicitly deleted, so the longer tiers
are built from on-demand backups tagged through `--description` and pruned daily.

Since v1.26.0 the policy is **scheduled**: the `fiveaday-backup-retention` Cloud Run Job runs
`manage.py backup_retention --apply` daily at 05:30 (see the Cloud Scheduler table above —
for months this section said "run daily" while nothing did, so the biweekly and monthly
points did not exist). `scripts/backup_retention.sh` remains the by-hand equivalent for a
workstation with gcloud:

```bash
python project/manage.py backup_retention               # dry run — show the plan
python project/manage.py backup_retention --apply       # what the scheduler runs
python project/manage.py backup_retention --apply --bootstrap   # seed both tiers today
./scripts/backup_retention.sh --dry-run                 # same policy, bash + gcloud
```

The calendar rules fire only on their day. Two honest limitations:

- Tiers **cannot be created retroactively** — an automated backup cannot be promoted to
  on-demand, so a monthly point only exists if the script ran on that day.
- With `KEEP_BIWEEKLY=1` the biweekly point ages 0→15 days and then resets, so just after the
  1st there is briefly no ~2-week-old copy. `KEEP_BIWEEKLY=2` gives continuous cover for one
  extra backup.

### Restoring — two different paths

```bash
gcloud sql backups list --instance=fiveaday-db --project=five-a-day-evolution

# (a) In place — OVERWRITES the instance, discards everything written since.
gcloud sql backups restore BACKUP_ID --restore-instance=fiveaday-db

# (b) Point in time — clones to a NEW instance, production untouched.
gcloud sql instances clone fiveaday-db fiveaday-db-recovery \
  --point-in-time '2026-08-27T09:00:00Z'
```

Prefer (b) when you need to *inspect* or recover a few rows; (a) is for a genuine
roll-the-whole-database-back. (a) is not reversible.

### `scripts/export_prod_db.sh` — full logical export

A complete `.sql.gz` dump of production, downloaded to a local directory.

**This script is gitignored and never pushed.** The dump contains personal data for real
students including minors — names, DNIs, phone numbers, addresses, allergies, payment
records and password hashes. Who holds the script is a deliberate per-person decision by the
maintainer.

Because it is untracked it travels with the machine rather than the repo, so it is normally
present wherever it has been granted — and absent on a fresh clone, in CI, or for anyone who
was not given it. Absence is expected in those cases and is not a broken checkout; ask the
maintainer for a copy rather than reconstructing it.

The destination is a **required argument with no default**, so a dump can never land
somewhere nobody chose:

```bash
./scripts/export_prod_db.sh --dest "<directory you choose>"
./scripts/export_prod_db.sh --dest "<dir>" --dry-run
```

It stages through a private, public-access-prevented GCS bucket, downloads, verifies the
archive (non-empty, valid gzip, core tables actually present), then **deletes the cloud
copy** so the only remaining copy is the local one. Run it outside class hours: the export
takes a serializable snapshot and briefly competes with live traffic on an `f1-micro`.

A per-database export does **not** include cluster-wide roles or passwords (Cloud SQL exposes
no `pg_dumpall --globals-only`), so restoring into a fresh instance means recreating the
`fiveaday_user` role first.

### `make backup` is not a production backup

`make backup` dumps the **local development** database out of the `db` container into
`backups/`. It never contacts Cloud SQL. The name is short for convenience; production
backups are the Cloud SQL ones above, and a portable production dump is
`scripts/export_prod_db.sh`.

### Known gaps

- **No copy outside Cloud SQL, and none outside this GCP project.** There is no scheduled
  logical export; everything depends on one managed system in one project. The manual export
  script is the only portable copy, and only when someone runs it.
- **Zonal instance.** A zone outage means downtime until Google recovers it. Data survives —
  backups are multi-region — but there is no automatic failover.

---

## 4. CI/CD — automated deploys

Two workflows, deliberately asymmetric. Testing is unattended because it is disposable;
production is never deployed by a timer.

| | `Deploy testing` | `Deploy production` |
|---|---|---|
| File | `.github/workflows/deploy-testing.yml` | `.github/workflows/deploy-production.yml` |
| Trigger | cron, 02:00 Europe/Madrid | push to `main` (the release PR merge) |
| Human in the loop | no | **yes — required reviewer** |
| Target | Compute Engine VM | Cloud Run + Cloud SQL |
| Credential | SSH deploy key (repo secret) | Workload Identity Federation (no stored key) |
| Manual run | `workflow_dispatch`, with a `force` input | `workflow_dispatch`, with a `force` input |

A third workflow, **`Rollback production`** (`rollback-production.yml`), is dispatch-only: it
rolls the service and every Cloud Run job back to a previous image tag behind the same
`production` approval gate. See [Rollback](#rollback--automatic-on-failure-on-demand-by-workflow).

Both deploy workflows are additions to the pipeline, not replacements: the `/deploy` skill is
still the by-hand path when you need to ship right now, and it is the documented fallback if a
workflow is broken.

> **Neither workflow does anything until it is on `main`.** `on: schedule` only ever fires for
> the repository's default branch, and the production workflow triggers on `push` to `main`.
> A change to either file takes effect only after it has travelled
> `development → testing → main` through the normal release path.

### Nightly testing deploy

The contract is a single equality: **the version answering `/health/` on the VM must equal
`pyproject.toml` on `origin/testing`**. Every night the workflow reads both and deploys only
when they differ, so a quiet night costs one `curl`.

```text
02:00-  →  check   read pyproject.toml on origin/testing
04:59
                  read /health/ on the VM
                  equal? stop. different (or unreachable)? continue
       →  deploy  record row counts
                  git pull --ff-only + docker compose up -d --build  (BOTH -f files)
                  gate: db mounted *_testing_postgres_data
                  gate: no dangling testing volume
                  wait for /health/ to report the new version (10 min budget)
                  reset ready_for_prod=false (locks the new version for production)
                  re-read row counts and diff against the baseline
       →  email   success or failure, to TESTING_NOTIFY_EMAILS
```

Three details that are load-bearing:

- **Two cron entries, and a WINDOW — not an exact hour.** GitHub cron is UTC, has no DST, and
  is best-effort: a tick routinely lands 10-90+ min late. `0 0 * * *` is 02:00 CEST / 01:00 CET
  and `0 1 * * *` is 03:00 CEST / 02:00 CET, and the first step accepts whichever tick lands
  inside **02:00-04:59 Madrid**. So both ticks are real attempts — two chances a night, in
  either DST regime — and if the first already deployed, the second's version compare finds
  nothing to do. The gate used to demand exactly `02`, which assumed the tick that *runs* is
  the tick that was *scheduled*; on 2026-09-02 GitHub delivered them at 01:48 and 05:29 UTC
  (03:48 and 07:29 Madrid), both were discarded, and no check ran at all. **That failure is
  green:** `check` reports `should_deploy=false` and `deploy` is *skipped*, so the run
  succeeds while the VM drifts. Verify a deploy with `/health/`, not with the green tick;
  `workflow_dispatch` bypasses the gate and is how you deploy off-window.
- **A dirty tree on the VM aborts *before* the pull.** `git status --porcelain` exits 0 on a
  dirty tree, so under `set -e` a bare call stops nothing and the pull clobbers the changes on
  the next line. The gate exits non-zero. If it fires, fix the VM — do not re-run without it.
- **The version check is necessary, not sufficient.** It passes even when the stack came up on
  the dev volume, because the *code* really did deploy. The volume assertion and the
  row-count diff are what actually prove the deploy is sound.

An unreachable VM counts as a reason to deploy: a stack that is not answering gets rebuilt.

**Every fresh deploy locks the version for production.** The deploy's last remote action runs
`manage.py set_ready_for_prod off` in the web container and asserts through
`/health/?deep=1` that `ready_for_prod` reads `false`. Only the **¿Listo para desplegar?**
button on `/testing/` (admin teacher, QA dashboard) sets it back to `true` — that click is
QA's sign-off, and the production workflow's preflight refuses to arm a release without it.
Because the nightly deploy resets the flag on every new version, a sign-off always refers to
the exact version it was given on; it can never silently cover a later, untested build.
Manual repair (both directions): `python manage.py set_ready_for_prod on|off` inside the
`fiveaday_django` container.

### Production deploy — armed automatically, shipped by hand

When the `testing → main` release PR merges, the workflow arms itself and then stops:

```text
push to main  →  preflight   read pyproject.toml on main + /health/ on production
                             GATE: /health/?deep=1 on TESTING must be healthy,
                                   serve this exact version, and report
                                   ready_for_prod=true (the QA sign-off)
                             already the same version in production? stop
                             WAIT for CI to go green on this exact commit (45 min budget)
                             list the migrations in this release
                             write it all to the run summary
              →  deploy      ⏸ BLOCKED on the `production` environment
                             (required reviewer + 5-minute wait timer)
```

So a release clears **two human gates**: QA's sign-off on the testing dashboard (phase 1,
checked automatically) and the required reviewer's approval on the run (phase 2). A
`workflow_dispatch` with `force=true` bypasses the sign-off gate as well as the version
compare — it exists for emergencies (e.g. redeploying the same version after an env-var
repair) and says so loudly in the log.

Nothing touches GCP until somebody opens the run and clicks **Review deployments →
production → Approve and deploy**. The automation does the waiting and the reporting; the
only manual act left is the decision.

The approval gate is enforced in two independent places, which is the point:

1. **GitHub** — the `production` environment has a required reviewer and a `main`-only branch
   policy, so the job will not start.
2. **Google Cloud** — the `fiveaday-deploy` service account's `workloadIdentityUser` binding
   is scoped to `principalSet://…/attribute.environment/production`. A job that does not
   declare `environment: production` cannot mint a token at all. This is why the preflight job
   is deliberately credential-free: it can read GitHub and public HTTP, and nothing else.

Once approved, the deploy runs the full ordering from the `/deploy` skill, with every check as
a hard gate:

| Step | Why it is in this order |
|---|---|
| Assert HEAD == the approved SHA | the runner checks out the exact commit, so the image tag always describes the code inside the image |
| Backup health (4 assertions) | deploying on a broken backup chain is the one state with no way back |
| Resolve the deployed image tag → migration diff | image tags are git short SHAs, so pending migrations are a `git diff` |
| Take a backup, assert `SUCCESSFUL` | an unverified backup is not a rollback plan |
| Pre-deploy `/health/?deep=1` fingerprint | gives the post-deploy report something to reconcile against |
| Build and push `web:<short-sha>` | skipped when the tag already exists — only the rollout was missing |
| Repoint **every** Cloud Run job, then verify | jobs pin their own tag; `gcloud run deploy` touches only the *service* |
| Assert every job's Cloud SQL attachment | a job on another instance would migrate somewhere invisible and exit 0 |
| Execute `fiveaday-migrate` | **before** the rollout, so new code never meets an old schema |
| `gcloud run deploy --image` and nothing else | `--set-env-vars` would replace the whole set and drop ~30 vars and 6 secret refs |
| Verify version, Cloud SQL, `DATABASE_URL` secret ref, `unapplied_migrations == 0` | a green shallow `/health/` proves only that the right *image* runs |

Two places the workflow is stricter than the by-hand procedure:

- **The Cloud Run job list is enumerated, not hard-coded.** A job added later would otherwise
  keep running last release's code forever, silently. The step fails if it finds fewer than 8.
- **Migrations run when they cannot be ruled out.** If the deployed image tag is not a commit
  in the repo the diff is impossible, so `fiveaday-migrate` runs anyway — `manage.py migrate`
  on an up-to-date schema is a no-op, whereas a skipped migration is not.

### Rollback — automatic on failure, on-demand by workflow

**Automatic.** If the deploy job fails *after* it has started changing production (the job
repoint is the first write), a rollback step repoints every Cloud Run job back at the
previous image and — when the rollout had already run — redeploys the service from that
image and re-verifies `/health/`. Its outcome (`revertido` / `fallido` / `manual` /
`inconcluso`) is reported in the run summary and the failure email. A failure *before* the
first write (backup checks, build) triggers no rollback because production was never touched.

**On-demand.** `.github/workflows/rollback-production.yml` (Actions → **Rollback
production**) is for the breakage discovered after a green deploy, or for finishing a failed
automatic rollback. Left empty, `image_tag` resolves to the most recent revision image that
differs from the serving one; passing a git short SHA targets exactly that build. It runs
under `environment: production`, so it needs the same approval click as a deploy (and could
not mint a WIF token otherwise), shares the deploy concurrency group so it can never race
one, and verifies `/health/` reports the version recorded in that commit's `pyproject.toml`.

Three rules both paths obey:

- **The database is never rolled back.** Migrations applied by the retired release stay
  applied — old code on the new schema is the same state every deploy passes through, since
  migrate runs before the rollout. Restoring the pre-deploy backup (its id is in the failure
  email) discards everything written since, so it stays a manual decision.
- **Rollback = redeploy the old image, never a traffic pin.** `update-traffic` to a named
  revision makes every future deploy land at 0 % traffic and silently fail its verify step.
- **Jobs and service move together.** A rollback that only touched the service would leave
  the 11 cron jobs running the retired release's code indefinitely — the same silent split
  the forward deploy guards against, inverted.

After any rollback, production reports the old version again, so re-shipping the fixed
release later needs no `force` — the preflight version compare arms itself normally.

### Credentials

Provisioned by **`scripts/setup_cicd.sh`** (idempotent; `--dry-run` prints every action and
changes nothing). Re-run it after rotating anything.

| Secret | Used by | Notes |
|---|---|---|
| `TESTING_VM_SSH_KEY` | testing | ed25519 private key for the `fiveaday-ci` user |
| `TESTING_VM_KNOWN_HOSTS` | testing | pins the VM host key; absent ⇒ falls back to `accept-new` |
| `TESTING_VM_HOST` / `TESTING_VM_USER` | testing | default to `34.26.130.187` / `fiveaday-ci` |
| `GCP_WIF_PROVIDER` | production | `projects/332600671945/locations/global/workloadIdentityPools/github/providers/github` |
| `GCP_DEPLOY_SA` | production | `fiveaday-deploy@five-a-day-evolution.iam.gserviceaccount.com` |
| `HEALTH_PROBE_TOKEN` | production | **optional.** Without it `/health/?deep=1` reports connectivity and migration state but no row counts, so a deploy cannot prove the data survived |
| `EMAIL_HOST_USER`, `EMAIL_SECRET`, `TESTING_NOTIFY_EMAILS`, `SUPPORT_EMAIL`, `TESTING_URL`, `PRODUCTION_URL` | both | pre-existing; shared with the other workflows |

**Why the testing pipeline uses a stored SSH key and production does not.** The nightly job is
the only unattended pipeline in this repo, so it is given no Google Cloud credential of any
kind — it cannot reach production even if it is compromised, and its blast radius is one
rebuildable VM. Revoking it is one line of instance metadata. Production, where a stolen
credential matters, uses federation and stores no key at all.

The public key lives in the VM's **instance** metadata under `fiveaday-ci`. The project-wide
`ssh-keys` entry (the maintainer's `Proye` key) is untouched; both scopes are additive.
`gcloud compute instances add-metadata` **replaces** the whole `ssh-keys` value, so the setup
script reads the existing entries out of JSON, carries them forward verbatim, and refuses to
write a value with fewer entries than it found. Do not hand-edit that key with a gcloud format
string — `--format='value[](…extract("value"))'` renders the list as a Python repr with
literal `\n`, and writing that back collapses every key into one corrupt line.

To revoke CI's access to the VM:

```bash
# Drop the fiveaday-ci line from the instance's ssh-keys metadata, or simply:
gh secret delete TESTING_VM_SSH_KEY --repo starseeker-code-public/five-a-day
```

To revoke CI's access to production:

```bash
gcloud iam service-accounts remove-iam-policy-binding \
  fiveaday-deploy@five-a-day-evolution.iam.gserviceaccount.com \
  --project=five-a-day-evolution --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/332600671945/locations/global/workloadIdentityPools/github/attribute.environment/production"
```

### The row-count fingerprint

Already configured. It is the only check that can distinguish a deploy onto the right database
from a deploy onto a valid but *wrong* one, so `Deploy production` uses it to compare student
and payment counts either side of a release.

The token is a **Secret Manager secret, not a plain env var** — it authenticates a caller to a
public endpoint, and every other secret here follows the same pattern, including the per-secret
`secretAccessor` binding that `fiveaday-run` needs because it holds no project-wide accessor.
The procedure is in [Store secrets in Secret Manager](#4-store-secrets-in-secret-manager).

To rotate it: add a new secret version, then re-run `gh secret set HEALTH_PROBE_TOKEN` with the
same value so GitHub and Cloud Run stay in agreement. A mismatch is not fatal — the deploy just
loses the row-count comparison and says so.

### Operating it

```bash
gh workflow run "Deploy testing" --ref main                      # check + deploy now
gh workflow run "Deploy testing" --ref main -f force=true        # redeploy the same version
gh workflow run "Deploy production" --ref main                   # re-arm; still needs approval
gh workflow run "Deploy production" --ref main -f force=true     # bypass version compare AND QA sign-off
gh workflow run "Rollback production" --ref main                 # roll back to the previous image
gh workflow run "Rollback production" --ref main -f image_tag=<sha>  # …or to an exact build
gh run list --workflow="Deploy production" --limit 5
gh run watch                                                     # follow the active run
```

Failures email `TESTING_NOTIFY_EMAILS` (testing) or the academy inbox plus `SUPPORT_EMAIL`
(production), and the run summary carries the version table, the migration list and the
rollback commands.

---

## 5. Cost Estimates

### GCP free credits — first 90 days

New GCP accounts receive **$300 USD in free credits, valid for 90 days**. At ~$15-27/month for
production, you will consume roughly $45-80 of the $300 before the window closes. The remaining
balance expires unused — credits cannot be extended or saved.

Use the free period to set up the production environment, validate Cloud Run + Cloud SQL integration,
configure secrets, domain, OAuth, and Cloud Scheduler jobs without spending real money.

### Permanent free tiers (never expire)

These quotas reset monthly and apply to all GCP accounts regardless of age:

| Service            | Free allowance per month             | Your usage                    |
|--------------------|--------------------------------------|-------------------------------|
| Compute Engine     | 1 e2-micro instance (US regions)     | Testing VM                    |
| Cloud Run          | 2M req + 180K vCPU-sec + 360K GB-sec | ~96K requests, ~29K vCPU-sec  |
| Cloud Build        | 120 build-minutes/day                | A few deploys/month           |
| Cloud Scheduler    | 3 jobs                               | First 3 periodic tasks        |
| Cloud Tasks        | 1M task executions                   | All async email sends         |
| Secret Manager     | 10K access operations                | ~100 ops                      |
| Artifact Registry  | 0.5 GB storage                       | ~0.5 GB Docker image          |
| Cloud Logging      | 50 GB ingestion                      | Well under                    |

Cloud Run's free tier alone covers **all traffic for 4 teachers** — the Django app itself costs
effectively $0 in compute. The unavoidable cost is Cloud SQL (always running, always charged).

### Ongoing monthly costs (production — after credits expire)

| Service                        | Config                              | Cost/month  |
|--------------------------------|-------------------------------------|-------------|
| Cloud Run (cold starts OK)     | 1 vCPU, 512 MB, min-0, max-2        | ~$0–2       |
| Cloud Run (always warm)        | same, min-1                         | ~$7–10      |
| Cloud SQL                      | db-f1-micro, 10 GB, daily backups   | ~$10–12     |
| Cloud Scheduler                | 20 jobs (17 beyond free 3)          | ~$1.70      |
| Cloud Tasks                    | async email queue                   | <$1         |
| Secret Manager + Artifact Reg. | 8 secrets, <1 GB images             | <$1         |
| Cloud DNS                      | 1 managed zone                      | ~$0.50      |
| **Total (min-0)**              |                                     | **~$15–18** |
| **Total (min-1, always warm)** |                                     | **~$22–27** |

Testing VM and local development: **$0/month**.

**min-0 vs min-1**: With `min-instances=0`, the container shuts down after inactivity. The first
request of the day takes 5-10 seconds (cold start). With `min-instances=1`, it stays warm 24/7 at
~$7/month. You can toggle this live without redeploying:

```bash
gcloud run services update fiveaday --min-instances=1 --region=$REGION
```

---

## 6. Optional Services for Future Evolution

These are not needed at launch. They are the natural next steps as the system or user base grows.

### Cloud Monitoring + uptime alerts

**Configured** as of 2026-09-01: an uptime check on `/health/`
(`five-a-day-uptime-8gdbTvEAAAI`, every 5 min, 10 s timeout), an email notification
channel (`SUPPORT_EMAIL`, channel `3290002934615318076`) and an alert policy
("Five a Day production down (/health/)", `18131874056285299811`) that fires when the
check fails for 5 minutes. To reproduce or adjust (the old
`uptime-check-configs create http` command form is retired):

```bash
# Create an uptime check on the health endpoint (shallow /health/, never the DB —
# a transient database blip must not page anyone; see the /health/ gotcha)
gcloud monitoring uptime create "Five a Day uptime" \
  --project=five-a-day-evolution \
  --resource-type=uptime-url \
  --resource-labels=host=fiveaday-332600671945.europe-southwest1.run.app,project_id=five-a-day-evolution \
  --protocol=https --path=/health/ --port=443 \
  --period=5 --timeout=10

# Inspect what exists
gcloud monitoring uptime list-configs --project=five-a-day-evolution
```

The notification channel and alert policy predate `gcloud monitoring` GA coverage — they
were created through the Monitoring REST API (`POST /v3/projects/$PROJECT/notificationChannels`
and `/v3/projects/$PROJECT/alertPolicies` with a bearer token from
`gcloud auth print-access-token`); the console (Monitoring → Alerting) edits them fine.

Cost: free for basic checks. Email alert notifications are free.

### Cloud Storage (GCS) — media files and bulk exports

Cloud Run containers have an ephemeral filesystem — any file written during a request is lost after
the container restarts. If you add student photo uploads, document storage, or bulk PDF/Excel
exports meant for download, those files need to live in Cloud Storage.

```bash
gcloud storage buckets create gs://fiveaday-media --location=europe-southwest1
```

`django-storages` (already a project dependency) supports GCS with minimal settings changes.

Cost: ~$0.02/GB/month. At this scale, effectively free.

### Sendgrid or Mailgun — high-volume email

Gmail SMTP allows 500 emails/day via App Password. This covers the current workload comfortably.
If you run large campaigns (announcements to all 1,000 students' families, bulk payment reminders),
you will hit that limit.

Sendgrid free tier: 100 emails/day. Paid: from ~$20/month for 50K emails/month. No code changes
needed — update `EMAIL_BACKEND` and credentials in Secret Manager.

### Cloud Armor — web application firewall

Protects against DDoS attacks and common web exploits (OWASP Top 10). Not justified for an
internal academy tool with 4 known users, but relevant if you ever open the app to students or
parents directly.

Cost: ~$6/month base + $0.75/million requests.

### Cloud CDN — static file acceleration

If you serve a student or parent portal with many simultaneous users downloading assets, a CDN in
front of static files reduces latency and Cloud Run load. WhiteNoise is sufficient at the current
scale.

Cost: ~$0.02/GB egress.

### Memorystore (Redis) — full async Celery

If the `CELERY_TASK_ALWAYS_EAGER` approach causes slow HTTP responses (e.g., a task that takes 10+
seconds) and the Cloud Scheduler/Tasks migration is not worth implementing, Memorystore is a managed
Redis that the existing Celery architecture connects to without code changes. Run the Celery worker
as a separate always-on Cloud Run service with a lightweight health check endpoint.

Cost: ~$20-25/month for the 1 GB basic tier. Only worth it if other options are exhausted.

### Vertex AI — intelligent features

The existing data model (students, payments, attendance, history) is well-suited for ML features:
automatic payment risk scoring, attendance pattern analysis, or natural language report generation.
Vertex AI provides managed models and pipelines when you are ready to explore this.

Cost: varies by usage and model.

### Cloud SQL read replica — reporting queries

If heavy reporting queries (bulk exports, analytics dashboards) start causing slow responses for
teachers, a read replica lets reporting queries run against a separate instance without affecting
the primary. At the current scale of 4 users this is not needed.

Cost: same tier as primary (~$10-12/month additional).

---

## Troubleshooting

### "could not translate host name 'db'"

The app is connecting to the Docker hostname `db` instead of Cloud SQL. Ensure `DATABASE_URL` is
set in Cloud Run env vars — it takes priority over any `.env` file values.

### Static files returning 404

The `entrypoint.sh` runs `collectstatic` automatically when `DJANGO_ENV=production` is set. If
static files are missing, confirm that env var is present in the Cloud Run service configuration.

### Google OAuth callback mismatch

`GOOGLE_REDIRECT_URI` in Cloud Run must match exactly what is configured in Google Cloud Console →
OAuth credentials → Authorized redirect URIs. Include the trailing slash. Both the Cloud Run URL
and the custom domain need to be listed if you use both.

### Slow cold starts

Set `--min-instances=1`. Adds ~$7/month but removes the 5-10 second delay on the first morning
request:

```bash
gcloud run services update fiveaday --min-instances=1 --region=$REGION
```

### Out of memory on the testing VM

Verify the swap file is active after a reboot:

```bash
free -h   # Swap row should show ~2 GB
```

If the swap is missing, the `/etc/fstab` entry was not saved. Re-run the swap setup commands and
verify the file persists with `cat /etc/fstab`.

### Cloud Scheduler job not firing

Check job status and last execution in the GCP Console → Cloud Scheduler, or via CLI:

```bash
gcloud scheduler jobs describe JOB_NAME --location=$REGION
gcloud scheduler jobs run JOB_NAME --location=$REGION   # Manual trigger for testing
```

Ensure the service account has `roles/run.invoker` on the Cloud Run Jobs resource.
