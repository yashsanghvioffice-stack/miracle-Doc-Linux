#!/bin/bash
# ══════════════════════════════════════════════════════════════
#  MIRACLE CLOUD GATEWAY -- DEPLOY SCRIPT (v2)
# ══════════════════════════════════════════════════════════════
#  Git-driven deployment for the gateway VM.
#
#  Usage:
#    sudo ./deploy.sh           # interactive, prompts to confirm
#    sudo ./deploy.sh --yes     # non-interactive (for cron / scripts)
#    sudo ./deploy.sh --dry-run # show what would happen, do nothing
#
#  Workflow:
#    1. Acquire exclusive lock (no concurrent deploys)
#    2. Git fetch + diff against origin
#    3. Halt if migration files are in the changeset (run manually first)
#    4. Restrict deploys to allowed dir prefixes (etc/, opt/, var/)
#    5. Backup existing files + track new files (manifest)
#    6. Copy files
#    7. nginx -t before reload; rollback on failure
#    8. Restart miracle-router; verify /health endpoint actually responds
#    9. Prune old backups (keep last 20)
#
#  Rollback covers: modified files (restored from .bak),
#                   new files (deleted).
#  Rollback does NOT cover: schema migrations, manual hot-fixes.
# ══════════════════════════════════════════════════════════════

set -eo pipefail

# ─── CONFIG ─────────────────────────────────────────────────────
REPO_DIR="/opt/miracle-repo"
BACKUP_ROOT="/opt/miracle-backup"
BACKUP_DIR="$BACKUP_ROOT/$(date +%Y%m%d_%H%M%S)"
LOG_FILE="/var/log/miracle-deploy.log"
LOCK_FILE="/var/run/miracle-deploy.lock"
ROUTER_HEALTH_URL="http://127.0.0.1:5001/health"
KEEP_BACKUPS=20

# Only files inside these top-level dirs get deployed.
# Anything outside these (README.md, deploy.sh, .gitignore, etc.)
# is silently ignored. Add new prefixes here as you expand.
ALLOWED_PREFIXES=("etc/" "opt/" "var/")

# Migration filename pattern. If git diff includes any file matching
# this, the deploy HALTS and the operator must run the migration first.
MIGRATION_PATTERN='^opt/miracle-router/migrate_.*\.py$'

# Flags
DRY_RUN=false
ASSUME_YES=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --yes|-y)  ASSUME_YES=true ;;
        --help|-h)
            grep -E "^#" "$0" | head -30
            exit 0
            ;;
    esac
done

# ─── BOOTSTRAP ──────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: must run as root (sudo)." >&2
    exit 1
fi

# Ensure log file exists and is locked-down
touch "$LOG_FILE" || { echo "Cannot create $LOG_FILE"; exit 1; }
chmod 640 "$LOG_FILE"

# ─── LOGGING ────────────────────────────────────────────────────
log() {
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[$ts] $1" | tee -a "$LOG_FILE"
}

# ─── LOCKING ────────────────────────────────────────────────────
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "ERROR: another deploy is already running (lock: $LOCK_FILE)." >&2
    exit 1
fi
# Lock is released automatically when the script exits

# ─── ROLLBACK ───────────────────────────────────────────────────
ROLLBACK_DONE=false

rollback() {
    # Guard against re-entry
    if $ROLLBACK_DONE; then return; fi
    ROLLBACK_DONE=true

    log "═══════════════════════════════════════════════════════"
    log "❌ ERROR DETECTED — Starting Rollback"
    log "═══════════════════════════════════════════════════════"

    if [ ! -d "$BACKUP_DIR" ]; then
        log "No backup dir found. Nothing to rollback."
        exit 1
    fi

    # 1. Restore modified files from manifest
    local manifest="$BACKUP_DIR/manifest.txt"
    if [ -f "$manifest" ]; then
        local bakfile
        while IFS='|' read -r _src dest; do
            bakfile="$BACKUP_DIR/$(echo "$dest" | tr '/' '_').bak"
            if [ -f "$bakfile" ]; then
                cp "$bakfile" "$dest"
                log "  Restored: $dest"
            fi
        done < "$manifest"
    fi

    # 2. Delete files that were newly created by this deploy
    local newfiles="$BACKUP_DIR/new_files.list"
    if [ -f "$newfiles" ]; then
        while read -r newfile; do
            if [ -f "$newfile" ]; then
                rm -f "$newfile"
                log "  Removed new file: $newfile"
            fi
        done < "$newfiles"
    fi

    # 3. Best-effort service recovery (don't recurse into rollback)
    systemctl daemon-reload || log "  WARN: daemon-reload failed"

    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx && log "  nginx reloaded after rollback"
    else
        log "  🚨 CRITICAL: nginx config STILL broken after rollback. Manual fix needed."
    fi

    systemctl restart miracle-router || log "  🚨 CRITICAL: miracle-router failed to restart after rollback"

    log "═══════════════════════════════════════════════════════"
    log "✅ Rollback complete. Backup preserved at: $BACKUP_DIR"
    log "═══════════════════════════════════════════════════════"
    exit 1
}

trap rollback ERR

# ═══════════════════════════════════════════════════════════════
log "═══════════════════════════════════════════════════════"
log "Deployment Started (dry_run=$DRY_RUN, assume_yes=$ASSUME_YES)"
log "═══════════════════════════════════════════════════════"

# ─── STEP 1: GIT FETCH + DIFF ───────────────────────────────────
log "[1/7] Checking for changes..."
cd "$REPO_DIR"

# Detect the upstream branch (don't assume master vs main)
UPSTREAM_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null \
    | sed 's@^refs/remotes/origin/@@' || echo "main")
log "  Upstream branch: origin/$UPSTREAM_BRANCH"

git fetch origin "$UPSTREAM_BRANCH"
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$UPSTREAM_BRANCH")

if [ "$LOCAL" = "$REMOTE" ]; then
    log "  No changes. Already at $(git rev-parse --short HEAD)."
    log "Nothing to deploy."
    exit 0
fi

# Get list of files changed/added between current HEAD and origin
CHANGED_FILES=$(git diff --name-only HEAD "origin/$UPSTREAM_BRANCH")
if [ -z "$CHANGED_FILES" ]; then
    log "  No file changes (commits only?). Pulling and exiting."
    git pull --ff-only origin "$UPSTREAM_BRANCH"
    exit 0
fi

log "  Changed files (${UPSTREAM_BRANCH}):"
echo "$CHANGED_FILES" | while read -r f; do log "    → $f"; done

# ─── STEP 2: SAFETY GATES ───────────────────────────────────────
log "[2/7] Safety checks..."

# Halt if any migration files are in the changeset
MIGRATION_FILES=$(echo "$CHANGED_FILES" | grep -E "$MIGRATION_PATTERN" || true)
if [ -n "$MIGRATION_FILES" ]; then
    log "═══════════════════════════════════════════════════════"
    log "❌ HALT: migration files detected in changeset."
    log "   Migrations alter schema and CANNOT be auto-rolled-back."
    log "   You must run these manually before deploying:"
    log ""
    echo "$MIGRATION_FILES" | while read -r m; do
        log "      sudo python3 /$m"
    done
    log ""
    log "   Then re-run this deploy script."
    log "═══════════════════════════════════════════════════════"
    exit 2
fi

# Filter the changeset to only allowed prefixes
DEPLOYABLE_FILES=""
SKIPPED_FILES=""
while IFS= read -r file; do
    [ -z "$file" ] && continue
    matched=false
    for prefix in "${ALLOWED_PREFIXES[@]}"; do
        if [[ "$file" == "$prefix"* ]]; then
            matched=true
            break
        fi
    done
    if $matched; then
        DEPLOYABLE_FILES+="$file"$'\n'
    else
        SKIPPED_FILES+="$file"$'\n'
    fi
done <<< "$CHANGED_FILES"

if [ -n "$SKIPPED_FILES" ]; then
    log "  Files not in deploy paths (skipping):"
    echo -n "$SKIPPED_FILES" | while read -r f; do log "    ⊘ $f"; done
fi

if [ -z "$DEPLOYABLE_FILES" ]; then
    log "  No deployable files in changeset. Pulling and exiting."
    git pull --ff-only origin "$UPSTREAM_BRANCH"
    exit 0
fi

DEPLOY_COUNT=$(echo -n "$DEPLOYABLE_FILES" | grep -c '^' || echo 0)
log "  Will deploy $DEPLOY_COUNT file(s)."

# ─── INTERACTIVE CONFIRMATION ───────────────────────────────────
if ! $ASSUME_YES && ! $DRY_RUN && [ -t 0 ]; then
    echo ""
    echo "Deploy $DEPLOY_COUNT file(s) listed above to PRODUCTION? (yes/no)"
    read -r confirm
    if [ "$confirm" != "yes" ]; then
        log "Cancelled by user."
        exit 0
    fi
fi

# ─── PULL THE CHANGES ───────────────────────────────────────────
if $DRY_RUN; then
    log "[DRY-RUN] would: git pull --ff-only origin $UPSTREAM_BRANCH"
else
    log "[3/7] Pulling from git..."
    git pull --ff-only origin "$UPSTREAM_BRANCH"
    log "  Now at: $(git rev-parse --short HEAD)"
fi

# ─── STEP 4: BACKUP + DEPLOY ────────────────────────────────────
log "[4/7] Backup + deploy..."
mkdir -p "$BACKUP_DIR"
MANIFEST="$BACKUP_DIR/manifest.txt"
NEWFILES_LIST="$BACKUP_DIR/new_files.list"
: > "$MANIFEST"
: > "$NEWFILES_LIST"

while IFS= read -r file; do
    [ -z "$file" ] && continue
    SRC="$REPO_DIR/$file"
    DEST="/$file"

    if [ ! -f "$SRC" ]; then
        # File was deleted in this changeset. Note: this script does
        # NOT auto-delete files from the server. Operator must remove
        # them manually if they truly should be gone.
        log "  ⊘ Source missing (deletion?), skipping: $SRC"
        continue
    fi

    # Track in manifest
    echo "$SRC|$DEST" >> "$MANIFEST"

    if [ -f "$DEST" ]; then
        # Existing file -- back up using path-mangled name for uniqueness
        local_bak="$BACKUP_DIR/$(echo "$DEST" | tr '/' '_').bak"
        if $DRY_RUN; then
            log "  [DRY-RUN] would back up: $DEST"
        else
            cp "$DEST" "$local_bak"
            log "  ✓ Backed up: $DEST"
        fi
    else
        # New file -- track for delete-on-rollback
        echo "$DEST" >> "$NEWFILES_LIST"
        log "  + New file: $DEST"
    fi

    if $DRY_RUN; then
        log "  [DRY-RUN] would copy: $SRC → $DEST"
    else
        mkdir -p "$(dirname "$DEST")"
        cp "$SRC" "$DEST"
        log "  ✓ Deployed: $DEST"
    fi
done <<< "$DEPLOYABLE_FILES"

if $DRY_RUN; then
    log "═══════════════════════════════════════════════════════"
    log "DRY-RUN complete. No changes made. No services restarted."
    log "═══════════════════════════════════════════════════════"
    exit 0
fi

# ─── STEP 5: NGINX VALIDATE + RELOAD ────────────────────────────
log "[5/7] Validating nginx config..."
if nginx -t 2>>"$LOG_FILE"; then
    log "  ✓ nginx config valid"
    systemctl reload nginx
    log "  ✓ nginx reloaded"
else
    log "  ✗ nginx config invalid -- triggering rollback"
    false  # trip the ERR trap
fi

# ─── STEP 6: RESTART ROUTER ─────────────────────────────────────
log "[6/7] Restarting miracle-router..."
systemctl daemon-reload
systemctl restart miracle-router

# ─── STEP 7: HEALTH CHECK ───────────────────────────────────────
log "[7/7] Health check..."

# Wait up to 15 seconds for the service to come up
HEALTH_OK=false
for attempt in 1 2 3 4 5; do
    sleep 3
    if ! systemctl is-active --quiet miracle-router; then
        log "  Attempt $attempt: miracle-router not active yet"
        continue
    fi
    # Actually call /health -- systemd is-active doesn't catch deadlocks
    HEALTH=$(curl -s --max-time 5 "$ROUTER_HEALTH_URL" || echo "")
    if echo "$HEALTH" | grep -q '"status":"ok"'; then
        log "  ✓ /health responded ok: $HEALTH"
        HEALTH_OK=true
        break
    else
        log "  Attempt $attempt: /health did not return ok yet"
    fi
done

if ! $HEALTH_OK; then
    log "  ✗ miracle-router /health never returned ok after 15s -- triggering rollback"
    false
fi

if ! systemctl is-active --quiet nginx; then
    log "  ✗ nginx not active after reload -- triggering rollback"
    false
fi

# ─── BACKUP RETENTION ───────────────────────────────────────────
log "Pruning old backups (keep $KEEP_BACKUPS)..."
# Use find + sort to handle paths safely
find "$BACKUP_ROOT" -maxdepth 1 -mindepth 1 -type d -printf '%T@ %p\n' 2>/dev/null \
    | sort -rn \
    | tail -n +$((KEEP_BACKUPS + 1)) \
    | cut -d' ' -f2- \
    | xargs -r rm -rf

# ═══════════════════════════════════════════════════════════════
log "═══════════════════════════════════════════════════════"
log "✅ Deployment Successful"
log "   Commit       : $(git rev-parse --short HEAD)"
log "   Backup       : $BACKUP_DIR"
log "   Files        : $DEPLOY_COUNT"
log "═══════════════════════════════════════════════════════"