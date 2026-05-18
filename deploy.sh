#!/bin/bash
set -e

# ══════════════════════════════════════════
#          MIRACLE SERVER DEPLOY
# ══════════════════════════════════════════

REPO_DIR="/opt/miracle-repo"
BACKUP_DIR="/opt/miracle-backup/$(date +%Y%m%d_%H%M%S)"
LOG_FILE="/var/log/miracle-deploy.log"

# Files to NEVER deploy
EXCLUDE=(".git" "deploy.sh" "README.md")

# ─── LOGGING ──────────────────────────────
log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a $LOG_FILE
}

# ─── ROLLBACK FUNCTION ────────────────────
rollback() {
  log "❌ ERROR DETECTED — Starting Rollback..."
  for bak in $BACKUP_DIR/*.bak; do
    [ -f "$bak" ] || continue
    original=$(basename "$bak" .bak)
    dest=$(find / -name "$original" \
      -not -path "*/miracle-backup/*" \
      -not -path "*/miracle-repo/*" 2>/dev/null | head -1)
    if [ -n "$dest" ]; then
      cp "$bak" "$dest"
      log "  Rolled back: $dest"
    fi
  done
  systemctl daemon-reload
  systemctl restart miracle-router || true
  nginx -t && systemctl reload nginx || true
  log "✅ Rollback Complete"
  exit 1
}

# Trigger rollback on any unexpected error
trap rollback ERR

# ══════════════════════════════════════════
log "========== Deployment Started =========="

# ─── STEP 1: CHECK FOR NEW CHANGES ────────
log "Checking for changes..."
cd $REPO_DIR
git fetch origin

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
  log "⚠️  No new changes found. Nothing to deploy."
  exit 0
fi

# Get list of changed/new files before pulling
CHANGED_FILES=$(git diff --name-only HEAD origin/master)
log "Changed files detected:"
for f in $CHANGED_FILES; do log "  → $f"; done

git pull origin main
log "✅ Git pull successful | Commit: $(git rev-parse --short HEAD)"

# ─── STEP 2: BACKUP ───────────────────────
log "Taking backup → $BACKUP_DIR"
mkdir -p $BACKUP_DIR

for file in $CHANGED_FILES; do
  DEST="/$file"
  if [ -f "$DEST" ]; then
    cp "$DEST" "$BACKUP_DIR/$(basename $DEST).bak"
    log "  Backed up: $DEST"
  fi
done
log "✅ Backup Done"

# ─── STEP 3: DEPLOY ───────────────────────
log "Deploying files..."

for file in $CHANGED_FILES; do

  # Skip excluded files
  skip=false
  for ex in "${EXCLUDE[@]}"; do
    [[ "$file" == *"$ex"* ]] && skip=true && break
  done
  if $skip; then
    log "  Skipped: $file"
    continue
  fi

  SRC="$REPO_DIR/$file"
  DEST="/$file"

  if [ ! -f "$SRC" ]; then
    log "  ⚠️  Source not found, skipping: $SRC"
    continue
  fi

  mkdir -p "$(dirname $DEST)"
  cp "$SRC" "$DEST"
  log "  ✅ Deployed: $SRC → $DEST"

done
log "✅ All Files Deployed"

# ─── STEP 4: RELOAD SERVICES ──────────────
log "Reloading services..."
systemctl daemon-reload

if nginx -t 2>/dev/null; then
  systemctl reload nginx
  log "✅ Nginx reloaded successfully"
else
  log "❌ Nginx config invalid!"
  rollback
fi

systemctl restart miracle-router
log "✅ miracle-router restarted"

# ─── STEP 5: HEALTH CHECK ─────────────────
log "Running health check..."
sleep 3

if systemctl is-active --quiet miracle-router; then
  log "✅ miracle-router is RUNNING"
else
  log "❌ miracle-router is DOWN!"
  rollback
fi

if systemctl is-active --quiet nginx; then
  log "✅ nginx is RUNNING"
else
  log "❌ nginx is DOWN!"
  rollback
fi

# ══════════════════════════════════════════
log "========== ✅ Deployment Successful =========="
log "Backup saved at : $BACKUP_DIR"
log "Full log at     : $LOG_FILE"