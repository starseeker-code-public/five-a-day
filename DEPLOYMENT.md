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

Keep these vars directly in `.env.testing` (alongside the rest of the testing config). There is no overlay file system — `.env.testing` is self-contained and is renamed to `.env` on the VM before bringing the stack up. It's gitignored via `.env*`.

Watch the logs after `docker compose up -d` for `✅ Teacher created/updated: ...` lines confirming the seeds landed. Gmail SMTP (`EMAIL_HOST_USER` + `EMAIL_SECRET`) must work in this environment: any seed block that omits `TEACHER_SEED_<N>_PASSWORD` requires the teacher to activate via the password-reset email.

#### 5. Routine updates

Use the `/deploy testing` skill, which does all of this plus the post-deploy version check.
By hand, from your workstation:

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
sudo docker compose -f $D/docker-compose.yml up -d --build
```

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
the baked-in value and would make this check lie — neither environment sets one today.

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

**Optional — `HEALTH_PROBE_TOKEN` (v1.16).** Not currently configured. It unlocks the
row-count fingerprint on `/health/?deep=1`, which is what lets a deploy prove the release did
not land on the wrong database. Without it the deep probe still reports connectivity and
migration state, just not the counts:

```bash
openssl rand -hex 32 | gcloud secrets create HEALTH_PROBE_TOKEN --data-file=-

# --update-secrets is ADDITIVE, unlike --set-env-vars, so it will not drop the
# existing env set. Verify the variable count before and after regardless.
gcloud run services update fiveaday --region=$REGION   --update-secrets=HEALTH_PROBE_TOKEN=HEALTH_PROBE_TOKEN:latest
```

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
  # Prefilled into the payment-reminder email forms
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
> code indefinitely. See [Routine deploys](#routine-deploys) for the full loop over all 8 jobs.

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
by hand.

Order matters: **migrate before the service rolls**, not after. Rolling the service first puts new
code in front of an old schema for the length of the rollout.

```bash
# Image tags are the git short SHA of the deployed commit
IMAGE=$REGION-docker.pkg.dev/$PROJECT_ID/fiveaday/web:$(git rev-parse --short origin/main)

# 1. Back up before touching production data
gcloud sql backups create --instance=fiveaday-db

# 2. Build new image
gcloud builds submit --tag $IMAGE .

# 3. Repoint ALL 8 jobs at the new image — they each pin their own tag
for JOB in fiveaday-migrate fiveaday-birthday-emails fiveaday-generate-payments \
           fiveaday-expenses-daily fiveaday-expenses-monthly fiveaday-funfriday-emails \
           fiveaday-monthly-report fiveaday-payment-reminders; do
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

Rollback is a traffic shift, no rebuild needed:

```bash
gcloud run revisions list --service=fiveaday --region=$REGION
gcloud run services update-traffic fiveaday --region=$REGION --to-revisions=<PREVIOUS>=100
```

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
are built from on-demand backups tagged through `--description` and pruned by
`scripts/backup_retention.sh`.

```bash
./scripts/backup_retention.sh --dry-run     # show the plan, change nothing
./scripts/backup_retention.sh               # apply, with a confirmation prompt
./scripts/backup_retention.sh --bootstrap   # seed both tiers today (first run)
```

Run daily for the calendar rules to fire. Two honest limitations:

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

## 4. Cost Estimates

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

## 5. Optional Services for Future Evolution

These are not needed at launch. They are the natural next steps as the system or user base grows.

### Cloud Monitoring + uptime alerts

Set up uptime checks so you are notified before teachers notice the app is down. No code changes
needed.

```bash
# Create an uptime check on the health endpoint
gcloud monitoring uptime-check-configs create http \
  --display-name="Five a Day uptime" \
  --http-check-path="/_health/" \
  --hostname=app.yourdomain.com
```

Cost: free for basic checks. Email alert notifications are free. Recommended as soon as the app
goes to production.

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
