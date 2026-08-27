#!/usr/bin/env bash
# =============================================================================
# backup_retention.sh — tiered retention for the production Cloud SQL backups
# =============================================================================
#
# WHY THIS EXISTS
#   Cloud SQL's own retention is a flat count of AUTOMATED backups — there is no
#   grandfather-father-son option. Left alone, the instance keeps the last 7
#   nightly backups and nothing older, so the recovery horizon is one week.
#
#   ON_DEMAND backups are exempt from that count and live until deleted, so the
#   longer tiers are built from on-demand backups tagged via --description, and
#   pruned here.
#
# THE POLICY
#   daily     7  AUTOMATED, managed natively by Cloud SQL (--retained-backups-count)
#   biweekly  1  ON_DEMAND "tier:biweekly", created on the 1st and the 16th
#   monthly   1  ON_DEMAND "tier:monthly",  created on the last day of the month
#   manual    3  ON_DEMAND with any other description (deploy backups), newest kept
#
#   Worked example, running on 28 August with the tiers already established:
#     dailies   28,27,26,25,24,23,22 Aug   (automated)
#     biweekly  16 Aug                     (created on the 16th)
#     monthly   31 Jul                     (created on the last day of July)
#     manual    up to 3 most recent deploy backups
#
# HONEST LIMITATION
#   Tiers cannot be created retroactively — an automated backup cannot be
#   promoted to on-demand. The 31 Jul point only exists if this ran on 31 Jul.
#   Use --bootstrap once to seed both tiers from today, then schedule daily.
#
#   With KEEP_BIWEEKLY=1 the biweekly point ages 0→15 days and resets, so right
#   after the 1st you briefly have no ~2-week-old copy. Set KEEP_BIWEEKLY=2 for
#   continuous cover at the cost of one extra backup.
#
# USAGE
#   ./scripts/backup_retention.sh --dry-run     # show the plan, change nothing
#   ./scripts/backup_retention.sh               # apply, with a confirmation
#   ./scripts/backup_retention.sh --yes         # apply, unattended (scheduler)
#   ./scripts/backup_retention.sh --bootstrap   # also seed both tiers today
# =============================================================================

set -euo pipefail

PROJECT="five-a-day-evolution"
INSTANCE="fiveaday-db"

KEEP_DAILY_AUTOMATED=7
KEEP_BIWEEKLY=1
KEEP_MONTHLY=1
KEEP_MANUAL=3

DRY_RUN=0
ASSUME_YES=0
BOOTSTRAP=0

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --yes | -y) ASSUME_YES=1 ;;
        --bootstrap) BOOTSTRAP=1 ;;
        -h | --help)
            sed -n '2,48p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            exit 2
            ;;
    esac
done

die() {
    echo "ERROR: $*" >&2
    exit 1
}
run() {
    if [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] $*"
    else
        "$@"
    fi
}

command -v gcloud >/dev/null 2>&1 || die "gcloud is not on PATH."
gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | grep -q . \
    || die "No active gcloud account. Run: gcloud auth login"

TODAY_DAY="$(date +%d)"
TOMORROW_DAY="$(date -d tomorrow +%d)"
IS_LAST_DAY_OF_MONTH=0
[ "$TOMORROW_DAY" = "01" ] && IS_LAST_DAY_OF_MONTH=1
IS_BIWEEKLY_DAY=0
{ [ "$TODAY_DAY" = "01" ] || [ "$TODAY_DAY" = "16" ]; } && IS_BIWEEKLY_DAY=1

echo "============================================================"
echo " Cloud SQL backup retention — $PROJECT / $INSTANCE"
echo " $(date '+%Y-%m-%d %H:%M')  (dry-run=$DRY_RUN bootstrap=$BOOTSTRAP)"
echo "============================================================"

# ── 1. Native automated retention ────────────────────────────────────────────
CURRENT_COUNT="$(gcloud sql instances describe "$INSTANCE" --project="$PROJECT" \
    --format='value(settings.backupConfiguration.backupRetentionSettings.retainedBackups)')"
echo "automated retention: currently $CURRENT_COUNT, want $KEEP_DAILY_AUTOMATED"
if [ "$CURRENT_COUNT" != "$KEEP_DAILY_AUTOMATED" ]; then
    run gcloud sql instances patch "$INSTANCE" --project="$PROJECT" \
        --retained-backups-count="$KEEP_DAILY_AUTOMATED" --quiet
fi

# ── 2. Snapshot current on-demand backups ────────────────────────────────────
# id|epoch|description  — newest first. AUTOMATED ones are Cloud SQL's problem.
LIST="$(gcloud sql backups list --instance="$INSTANCE" --project="$PROJECT" \
    --filter='type=ON_DEMAND AND status=SUCCESSFUL' \
    --format='csv[no-heading](id,windowStartTime,description)' 2>/dev/null || true)"

classify() { # $1 = description -> biweekly | monthly | manual
    case "$1" in
        tier:biweekly*) echo biweekly ;;
        tier:monthly*) echo monthly ;;
        *) echo manual ;;
    esac
}

age_days() { # $1 = ISO timestamp
    local then now
    then="$(date -d "$1" +%s 2>/dev/null || echo 0)"
    now="$(date +%s)"
    [ "$then" -eq 0 ] && {
        echo 99999
        return
    }
    echo $(((now - then) / 86400))
}

# ── 3. Create tier backups when the calendar says so ─────────────────────────
create_tier() { # $1 = biweekly|monthly
    echo "==> creating tier:$1 backup"
    run gcloud sql backups create --instance="$INSTANCE" --project="$PROJECT" \
        --description="tier:$1 $(date +%Y-%m-%d)" --quiet
}

if [ "$BOOTSTRAP" = "1" ]; then
    create_tier biweekly
    create_tier monthly
else
    [ "$IS_BIWEEKLY_DAY" = "1" ] && create_tier biweekly || true
    [ "$IS_LAST_DAY_OF_MONTH" = "1" ] && create_tier monthly || true
    if [ "$IS_BIWEEKLY_DAY" = "0" ] && [ "$IS_LAST_DAY_OF_MONTH" = "0" ]; then
        echo "==> not a tier day (need the 1st, the 16th, or month end); pruning only"
    fi
fi

# Re-read so anything just created is included in the prune below.
if [ "$DRY_RUN" != "1" ]; then
    LIST="$(gcloud sql backups list --instance="$INSTANCE" --project="$PROJECT" \
        --filter='type=ON_DEMAND AND status=SUCCESSFUL' \
        --format='csv[no-heading](id,windowStartTime,description)' 2>/dev/null || true)"
fi

# ── 4. Prune each class down to its keep-count ───────────────────────────────
prune_class() { # $1 = class, $2 = keep
    local class="$1" keep="$2" seen=0 id ts desc kind
    echo "--- $class (keep $keep) ---"
    while IFS=',' read -r id ts desc; do
        [ -n "${id:-}" ] || continue
        kind="$(classify "${desc:-}")"
        [ "$kind" = "$class" ] || continue
        seen=$((seen + 1))
        if [ "$seen" -le "$keep" ]; then
            printf '    KEEP   %s  %s  (%s days old)\n' "$id" "$ts" "$(age_days "$ts")"
        else
            printf '    DELETE %s  %s  (%s days old)\n' "$id" "$ts" "$(age_days "$ts")"
            run gcloud sql backups delete "$id" --instance="$INSTANCE" \
                --project="$PROJECT" --quiet
        fi
    done <<EOF
$LIST
EOF
    [ "$seen" -gt 0 ] || echo "    (none yet)"
}

if [ "$ASSUME_YES" != "1" ] && [ "$DRY_RUN" != "1" ]; then
    echo
    echo "About to prune on-demand backups to: biweekly=$KEEP_BIWEEKLY"
    echo "monthly=$KEEP_MONTHLY manual=$KEEP_MANUAL. Deleted backups are gone."
    printf 'Continue? [y/N] '
    read -r reply
    case "$reply" in y | Y | yes) ;; *) die "Aborted." ;; esac
fi

echo
prune_class monthly "$KEEP_MONTHLY"
prune_class biweekly "$KEEP_BIWEEKLY"
prune_class manual "$KEEP_MANUAL"

echo
echo "============================================================"
echo " Resulting recovery horizon"
gcloud sql backups list --instance="$INSTANCE" --project="$PROJECT" \
    --filter='status=SUCCESSFUL' \
    --format='table(id,type,windowStartTime,description)' 2>/dev/null | head -20
echo "============================================================"
echo " Point-in-time recovery covers the last 7 days on top of these,"
echo " restoring by CLONING to a new instance (gcloud sql instances clone)."
