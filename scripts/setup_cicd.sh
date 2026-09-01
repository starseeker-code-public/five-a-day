#!/usr/bin/env bash
#
# setup_cicd.sh — provision the credentials the deploy workflows need.
#
# Two very different mechanisms, on purpose:
#
#   TESTING   .github/workflows/deploy-testing.yml  (nightly, unattended)
#             A dedicated SSH deploy key. The nightly job gets NO Google Cloud
#             credential of any kind, so the one unattended pipeline in this
#             repo cannot reach production even if it is compromised. Blast
#             radius is one rebuildable VM; revoking is one metadata line.
#
#   PRODUCTION .github/workflows/deploy-production.yml  (manual approval)
#             Workload Identity Federation — no long-lived key anywhere. The
#             service-account binding is scoped to the `production` GitHub
#             environment, so a token can only be minted by a job that has
#             already passed the required-reviewer gate. A workflow that omits
#             `environment: production` gets nothing.
#
# Idempotent: safe to re-run. Existing resources are reported, not recreated.
#
# Usage:
#   scripts/setup_cicd.sh --dry-run          # print every action, change nothing
#   scripts/setup_cicd.sh                    # provision everything
#   scripts/setup_cicd.sh --testing-only
#   scripts/setup_cicd.sh --production-only
#   scripts/setup_cicd.sh --rotate-testing-key   # replace the VM SSH key
#
# A re-run does NOT rotate the testing SSH key. It is left alone when the public
# half is in instance metadata AND the private half is in TESTING_VM_SSH_KEY;
# use --rotate-testing-key to replace it deliberately.
#
set -euo pipefail

# ---------------------------------------------------------------- constants
PROJECT=five-a-day-evolution
PROJECT_NUMBER=332600671945
REGION=europe-southwest1

VM_NAME=fiveaday-testing
VM_ZONE=us-east1-c
VM_IP=34.26.130.187
CI_SSH_USER=fiveaday-ci

REPO=starseeker-code-public/five-a-day
POOL=github
PROVIDER=github
DEPLOY_SA=fiveaday-deploy
DEPLOY_SA_EMAIL="${DEPLOY_SA}@${PROJECT}.iam.gserviceaccount.com"

DRY_RUN=false
DO_TESTING=true
DO_PRODUCTION=true
ROTATE_KEY=false

for arg in "$@"; do
  case "$arg" in
    --dry-run)            DRY_RUN=true ;;
    --testing-only)       DO_PRODUCTION=false ;;
    --production-only)    DO_TESTING=false ;;
    --rotate-testing-key) ROTATE_KEY=true ;;
    -h|--help)            sed -n '2,36p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# ---------------------------------------------------------------- helpers
bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$*"; }
skip() { printf '  \033[36mexists\033[0m %s\n' "$*"; }

run() {
  if [ "$DRY_RUN" = true ]; then
    printf '  \033[90m[dry-run]\033[0m %s\n' "$*"
  else
    printf '  \033[32m$\033[0m %s\n' "$*"
    "$@"
  fi
}

# Same as run(), but for commands whose argument contains secret material —
# prints a redacted form so the log stays shareable.
run_secret() {
  local label="$1"; shift
  if [ "$DRY_RUN" = true ]; then
    printf '  \033[90m[dry-run]\033[0m %s\n' "$label"
  else
    printf '  \033[32m$\033[0m %s\n' "$label"
    "$@"
  fi
}

# ---------------------------------------------------------------- preflight
bold "Preflight"

command -v gcloud >/dev/null || { echo "gcloud is not installed" >&2; exit 1; }
command -v gh     >/dev/null || { echo "gh (GitHub CLI) is not installed" >&2; exit 1; }
command -v ssh-keygen >/dev/null || { echo "ssh-keygen is not installed" >&2; exit 1; }

# Used to parse the VM's instance metadata — see the ssh-keys block in Part A
# for why gcloud's own format strings are not safe for that value. Probe by
# EXECUTING each candidate: on Windows/Git Bash `command -v python3` happily
# resolves to the Microsoft Store stub, which is on PATH but is not Python.
PY_BIN=''
for candidate in python3 python py; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import json' >/dev/null 2>&1; then
    PY_BIN="$candidate"
    break
  fi
done
[ -n "$PY_BIN" ] || { echo "No working python found (needed to parse VM metadata safely)" >&2; exit 1; }

ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format='value(account)' | head -1)
info "gcloud account: ${ACCOUNT:-<none>}"
[ -n "$ACCOUNT" ] || { echo "No active gcloud account. Run: gcloud auth login" >&2; exit 1; }

gh auth status >/dev/null 2>&1 || { echo "gh is not authenticated. Run: gh auth login" >&2; exit 1; }
info "github repo: $REPO"
[ "$DRY_RUN" = true ] && warn "DRY RUN — nothing will be changed."

# ================================================================
# PART A — testing: dedicated SSH deploy key
# ================================================================
if [ "$DO_TESTING" = true ]; then
  bold "A. Testing VM — dedicated SSH deploy key"

  KEYDIR=$(mktemp -d)
  trap 'rm -rf "$KEYDIR"' EXIT
  KEYFILE="$KEYDIR/id_ed25519"

  # --- read the VM's INSTANCE metadata FIRST -------------------------------
  #
  # The read has to come before key generation, because whether a key is needed
  # at all depends on what is already there. Generating first and deciding later
  # is what made "idempotent" false: every re-run minted a new keypair and
  # silently rotated CI's access.
  #
  # Instance metadata, not project metadata: the project-wide `ssh-keys` entry
  # carries the maintainer's `Proye` key and must not be touched. Both scopes
  # are additive on the VM, so an instance-level entry grants access without
  # displacing anything.
  #
  # NOTE the read/write asymmetry below.
  # `add-metadata --metadata ssh-keys=...` REPLACES the whole ssh-keys value, so
  # the existing entries have to be read and carried forward VERBATIM. Getting
  # this wrong locks the maintainer out of the VM, which is why the value is
  # round-tripped through JSON and not through a gcloud format string:
  # `--format='value[](...extract("value"))'` renders the list as a Python repr
  # (`['a\nb']`) with literal backslash-n, and writing that back would collapse
  # every existing key into one corrupt line.
  gcloud compute instances describe "$VM_NAME" --zone="$VM_ZONE" --project="$PROJECT" \
    --format=json > "$KEYDIR/instance.json"
  "$PY_BIN" -c '
import json, sys
inst = json.load(open(sys.argv[1], encoding="utf-8"))
for item in inst.get("metadata", {}).get("items", []):
    if item["key"] == "ssh-keys":
        sys.stdout.write(item.get("value", ""))
' "$KEYDIR/instance.json" > "$KEYDIR/ssh-keys.old"

  EXISTING_COUNT=$(grep -c ':' "$KEYDIR/ssh-keys.old" || true)
  info "instance metadata currently holds $EXISTING_COUNT ssh-keys entr(ies)"

  # --- decide whether a key is needed at all -------------------------------
  #
  # A working key must exist in BOTH places to be usable: the public half in
  # instance metadata and the private half in the GitHub secret. Either one
  # missing means CI cannot connect, so the pair has to be reissued.
  #
  # `grep -q X && VAR=true` on its own line is NOT safe under `set -e`: when grep
  # finds nothing the line's status is non-zero and the script exits. Use an if.
  HAVE_METADATA=false
  HAVE_SECRET=false
  if grep -q "^${CI_SSH_USER}:" "$KEYDIR/ssh-keys.old"; then HAVE_METADATA=true; fi
  if gh secret list --repo "$REPO" | grep -q '^TESTING_VM_SSH_KEY'; then HAVE_SECRET=true; fi

  ISSUE_KEY=true
  if [ "$ROTATE_KEY" = true ]; then
    info "--rotate-testing-key given: issuing a new keypair and replacing the old one"
  elif [ "$HAVE_METADATA" = true ] && [ "$HAVE_SECRET" = true ]; then
    ISSUE_KEY=false
    skip "SSH key for $CI_SSH_USER (in instance metadata and in TESTING_VM_SSH_KEY)"
    info "keeping it — pass --rotate-testing-key to replace it"
  elif [ "$HAVE_METADATA" = true ]; then
    # The private half is unrecoverable, so the metadata entry is dead weight.
    warn "instance metadata has a $CI_SSH_USER key but TESTING_VM_SSH_KEY is missing."
    warn "The private half cannot be recovered — reissuing the pair."
  elif [ "$HAVE_SECRET" = true ]; then
    warn "TESTING_VM_SSH_KEY exists but the VM has no $CI_SSH_USER entry — reissuing the pair."
  fi

  if [ "$ISSUE_KEY" = false ]; then
    info "skipping key generation and the metadata write"
  elif [ "$DRY_RUN" = true ]; then
    info "[dry-run] would generate an ed25519 keypair for $CI_SSH_USER"
    info "[dry-run] would rewrite ssh-keys as: the $EXISTING_COUNT existing entr(ies) minus any"
    info "[dry-run] $CI_SSH_USER entry, plus one fresh $CI_SSH_USER key"
  else
    ssh-keygen -t ed25519 -N '' -C "github-actions@$REPO" -f "$KEYFILE" -q
    info "generated a fresh ed25519 keypair (never written outside $KEYDIR)"

    PUBKEY=$(cut -d' ' -f1,2 < "${KEYFILE}.pub")
    {
      grep -v "^${CI_SSH_USER}:" "$KEYDIR/ssh-keys.old" || true
      printf '%s:%s github-actions\n' "$CI_SSH_USER" "$PUBKEY"
    } | sed '/^[[:space:]]*$/d' > "$KEYDIR/ssh-keys.new"

    NEW_COUNT=$(grep -c ':' "$KEYDIR/ssh-keys.new" || true)
    # Guard against the exact failure this block exists to prevent.
    if [ "$NEW_COUNT" -lt "$EXISTING_COUNT" ]; then
      echo "ABORT: the new ssh-keys value has $NEW_COUNT entries, fewer than the" >&2
      echo "$EXISTING_COUNT already present. Refusing to write — that would revoke access." >&2
      exit 1
    fi
    info "writing $NEW_COUNT ssh-keys entries to instance metadata"
    gcloud compute instances add-metadata "$VM_NAME" --zone="$VM_ZONE" --project="$PROJECT" \
      --metadata-from-file "ssh-keys=$KEYDIR/ssh-keys.new"
  fi

  # --- pin the host key ---------------------------------------------------
  # Without this the workflow falls back to accept-new, which trusts whatever
  # answers on the first connection.
  if [ "$DRY_RUN" = true ]; then
    info "[dry-run] would ssh-keyscan $VM_IP into the TESTING_VM_KNOWN_HOSTS secret"
  else
    ssh-keyscan -T 20 -t rsa,ecdsa,ed25519 "$VM_IP" > "$KEYDIR/known_hosts" 2>/dev/null || true
    if [ -s "$KEYDIR/known_hosts" ]; then
      info "scanned $(grep -c . "$KEYDIR/known_hosts") host keys from $VM_IP"
    else
      warn "ssh-keyscan returned nothing — the workflow will use StrictHostKeyChecking=accept-new"
    fi
  fi

  # --- store the secrets --------------------------------------------------
  #
  # TESTING_VM_SSH_KEY is written only when a key was actually issued: there is
  # no private half to upload otherwise. The rest are cheap to refresh.
  if [ "$DRY_RUN" = true ]; then
    info "[dry-run] gh secret set TESTING_VM_KNOWN_HOSTS / TESTING_VM_HOST / TESTING_VM_USER"
    [ "$ISSUE_KEY" = true ] && info "[dry-run] gh secret set TESTING_VM_SSH_KEY" || true
  else
    if [ "$ISSUE_KEY" = true ]; then
      run_secret "gh secret set TESTING_VM_SSH_KEY" \
        gh secret set TESTING_VM_SSH_KEY --repo "$REPO" < "$KEYFILE"
    fi
    if [ -s "$KEYDIR/known_hosts" ]; then
      run_secret "gh secret set TESTING_VM_KNOWN_HOSTS" \
        gh secret set TESTING_VM_KNOWN_HOSTS --repo "$REPO" < "$KEYDIR/known_hosts"
    fi
    printf '%s' "$VM_IP"      | gh secret set TESTING_VM_HOST --repo "$REPO"
    printf '%s' "$CI_SSH_USER" | gh secret set TESTING_VM_USER --repo "$REPO"
    info "secrets stored"
  fi

  # --- the GitHub environment (deployment history only, no protection) ----
  if gh api "repos/$REPO/environments/testing" >/dev/null 2>&1; then
    skip "GitHub environment 'testing'"
  else
    run gh api --method PUT "repos/$REPO/environments/testing" --input /dev/null
  fi

  # --- verify -------------------------------------------------------------
  #
  # Only possible when a key was issued this run: the private half is thrown
  # away with $KEYDIR, and GitHub secrets are write-only, so an existing key
  # cannot be read back to test it. When it is skipped, the nightly workflow
  # itself is the test — dispatch it manually.
  if [ "$ISSUE_KEY" = false ]; then
    info "no key issued this run, so it cannot be tested from here. To check it end to end:"
    info "  gh workflow run \"Deploy testing\" --ref main -f force=true"
  elif [ "$DRY_RUN" = false ]; then
    info "verifying the key works (sudo + docker + git on the VM)…"
    if ssh -i "$KEYFILE" -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
         -o ConnectTimeout=30 "${CI_SSH_USER}@${VM_IP}" \
         'sudo -n true && sudo docker ps --format "{{.Names}}" | head -5 && sudo git -c safe.directory=* -C /home/Proye/five-a-day log --oneline -1'
    then
      info "VM access confirmed"
    else
      warn "could not verify VM access. Metadata keys can take ~30s to propagate — retry:"
      warn "  ssh ${CI_SSH_USER}@${VM_IP}"
      warn "If it keeps failing, check that the guest agent added $CI_SSH_USER to google-sudoers."
    fi
  fi
fi

# ================================================================
# PART B — production: Workload Identity Federation
# ================================================================
if [ "$DO_PRODUCTION" = true ]; then
  bold "B. Production — Workload Identity Federation"

  run gcloud services enable iamcredentials.googleapis.com sts.googleapis.com \
    --project="$PROJECT"

  # --- pool ---------------------------------------------------------------
  if gcloud iam workload-identity-pools describe "$POOL" --location=global \
       --project="$PROJECT" >/dev/null 2>&1; then
    skip "workload identity pool '$POOL'"
  else
    run gcloud iam workload-identity-pools create "$POOL" \
      --location=global --project="$PROJECT" \
      --display-name="GitHub Actions" \
      --description="OIDC federation for GitHub Actions workflows in $REPO"
  fi

  # --- provider -----------------------------------------------------------
  #
  # The attribute CONDITION is the outer fence: no token from any other
  # repository can be exchanged at all. `attribute.environment` is what the
  # service-account binding below keys on, which is why it must be mapped here.
  if gcloud iam workload-identity-pools providers describe "$PROVIDER" \
       --workload-identity-pool="$POOL" --location=global \
       --project="$PROJECT" >/dev/null 2>&1; then
    skip "provider '$PROVIDER'"
  else
    run gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
      --workload-identity-pool="$POOL" --location=global --project="$PROJECT" \
      --display-name="GitHub OIDC" \
      --issuer-uri="https://token.actions.githubusercontent.com" \
      --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner,attribute.ref=assertion.ref,attribute.environment=assertion.environment" \
      --attribute-condition="assertion.repository == '${REPO}'"
  fi

  # --- deploy service account --------------------------------------------
  if gcloud iam service-accounts describe "$DEPLOY_SA_EMAIL" \
       --project="$PROJECT" >/dev/null 2>&1; then
    skip "service account $DEPLOY_SA_EMAIL"
  else
    run gcloud iam service-accounts create "$DEPLOY_SA" --project="$PROJECT" \
      --display-name="GitHub Actions production deploy" \
      --description="Impersonated only by a job running in the 'production' GitHub environment"
  fi

  # --- roles --------------------------------------------------------------
  #
  # run.admin          deploy the service, update + execute all Cloud Run jobs
  # cloudbuild.editor  gcloud builds submit
  # artifactregistry   push the image and list existing tags
  # cloudsql.editor    create and describe the pre-deploy backup
  # logging.viewer     stream build logs back into the Actions log
  # serviceusage       Cloud Build API usage from a federated identity
  # serviceAccountUser act as fiveaday-run (the service's runtime identity) and
  #                    as the Cloud Build service account. Project-scoped
  #                    because Cloud Build's default SA is resolved at build
  #                    time; run.admin already implies the ability to run code
  #                    as fiveaday-run, so this widens little in practice.
  for ROLE in \
    roles/run.admin \
    roles/cloudbuild.builds.editor \
    roles/artifactregistry.writer \
    roles/cloudsql.editor \
    roles/logging.viewer \
    roles/serviceusage.serviceUsageConsumer \
    roles/iam.serviceAccountUser
    # --format=none: add-iam-policy-binding echoes the entire project policy on
    # every call, which buries the handful of lines that actually matter.
  do
    run gcloud projects add-iam-policy-binding "$PROJECT" \
      --member="serviceAccount:${DEPLOY_SA_EMAIL}" --role="$ROLE" \
      --condition=None --quiet --format=none
  done

  # Cloud Build stages the uploaded source in a bucket. Scope the grant to that
  # bucket rather than handing out project-wide storage access.
  BUCKET="gs://${PROJECT}_cloudbuild"
  if gcloud storage buckets describe "$BUCKET" --project="$PROJECT" >/dev/null 2>&1; then
    run gcloud storage buckets add-iam-policy-binding "$BUCKET" \
      --member="serviceAccount:${DEPLOY_SA_EMAIL}" --role=roles/storage.objectAdmin \
      --project="$PROJECT" --format=none
  else
    warn "$BUCKET does not exist yet. Cloud Build creates it on the first build,"
    warn "which needs storage.buckets.create — grant it once, or run one build by hand:"
    warn "  gcloud projects add-iam-policy-binding $PROJECT \\"
    warn "    --member=serviceAccount:${DEPLOY_SA_EMAIL} --role=roles/storage.admin"
  fi

  # --- the binding that makes the approval gate load-bearing -------------
  #
  # principalSet keyed on attribute.environment/production. Combined with the
  # provider's repository condition this reads: "a GitHub Actions job in THIS
  # repo whose job declares `environment: production`". Since that environment
  # requires a reviewer, no token exists until a human has approved the run —
  # and the credential-free preflight job cannot obtain one at all.
  PRINCIPAL="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.environment/production"
  run gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA_EMAIL" \
    --project="$PROJECT" --role=roles/iam.workloadIdentityUser \
    --member="$PRINCIPAL" --quiet --format=none

  # --- secrets ------------------------------------------------------------
  PROVIDER_RESOURCE="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
  if [ "$DRY_RUN" = true ]; then
    info "[dry-run] gh secret set GCP_WIF_PROVIDER=$PROVIDER_RESOURCE"
    info "[dry-run] gh secret set GCP_DEPLOY_SA=$DEPLOY_SA_EMAIL"
  else
    printf '%s' "$PROVIDER_RESOURCE" | gh secret set GCP_WIF_PROVIDER --repo "$REPO"
    printf '%s' "$DEPLOY_SA_EMAIL"   | gh secret set GCP_DEPLOY_SA   --repo "$REPO"
    info "secrets stored"
  fi

  # --- confirm the approval gate is actually in place --------------------
  bold "B2. Production environment protection"
  RULES=$(gh api "repos/$REPO/environments/production" \
    --jq '[.protection_rules[].type] | join(",")' 2>/dev/null || echo '')
  info "protection rules: ${RULES:-<none>}"
  if printf '%s' "$RULES" | grep -q required_reviewers; then
    info "required_reviewers is set — the deploy job will block for approval"
  else
    warn "NO required_reviewers on the 'production' environment!"
    warn "Without it the deploy job runs unattended. Add a reviewer at:"
    warn "  https://github.com/$REPO/settings/environments"
  fi
  BRANCHES=$(gh api "repos/$REPO/environments/production/deployment-branch-policies" \
    --jq '[.branch_policies[].name] | join(",")' 2>/dev/null || echo '')
  info "allowed branches: ${BRANCHES:-<any>}"
  [ "$BRANCHES" = "main" ] || warn "expected the branch policy to be exactly 'main'"

  # --- optional: the row-count fingerprint ------------------------------
  if gh secret list --repo "$REPO" | grep -q '^HEALTH_PROBE_TOKEN'; then
    skip "HEALTH_PROBE_TOKEN secret"
  else
    warn "HEALTH_PROBE_TOKEN is not set. /health/?deep=1 will report connectivity and"
    warn "migration state but NOT row counts, so a deploy cannot prove the data survived."
    warn "It is a Secret Manager secret (it authenticates a caller to a public endpoint),"
    warn "so it needs a per-secret binding: fiveaday-run has no project-wide accessor."
    warn "  TOKEN=\$(openssl rand -hex 32)"
    warn "  printf '%s' \"\$TOKEN\" | gcloud secrets create HEALTH_PROBE_TOKEN \\"
    warn "    --project=$PROJECT --replication-policy=automatic --data-file=-"
    warn "  gcloud secrets add-iam-policy-binding HEALTH_PROBE_TOKEN --project=$PROJECT \\"
    warn "    --member=serviceAccount:fiveaday-run@$PROJECT.iam.gserviceaccount.com \\"
    warn "    --role=roles/secretmanager.secretAccessor"
    warn "  gcloud run services update fiveaday --project=$PROJECT --region=$REGION \\"
    warn "    --update-secrets=HEALTH_PROBE_TOKEN=HEALTH_PROBE_TOKEN:latest   # ADDITIVE"
    warn "  printf '%s' \"\$TOKEN\" | gh secret set HEALTH_PROBE_TOKEN --repo $REPO"
  fi
fi

# ================================================================
bold "Done"
cat <<'SUMMARY'
  Reminder: both workflows live on the DEFAULT branch to take effect.
  `on: schedule` only ever fires for the default branch, so the nightly
  testing deploy stays inert until this change has travelled
  development → testing → main via the normal release path.

  Verify afterwards:
    gh workflow run "Deploy testing" --ref main          # force a check now
    gh secret list
SUMMARY
