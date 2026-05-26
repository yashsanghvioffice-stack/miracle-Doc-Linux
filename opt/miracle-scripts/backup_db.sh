#!/bin/bash
# ══════════════════════════════════════════════════════════════
#  MIRACLE CLOUD GATEWAY -- SQLite Backup Script
# ══════════════════════════════════════════════════════════════
#  Designed to run nightly via cron. Safe to run anytime --
#  uses sqlite3 .backup (online backup, doesn't lock writes).
#
#  Usage:
#    sudo ./backup-db.sh              # full backup + retention + S3 push
#    sudo ./backup-db.sh --local-only # skip S3 push
#    sudo ./backup-db.sh --verify     # restore + integrity check the latest
#
#  Install as cron:
#    sudo crontab -e
#    # daily at 02:30 (low traffic):
#    30 2 * * * /opt/miracle-scripts/backup_db.sh >> /var/log/miracle-backup.log 2>&1
#
#  What it does:
#    1. sqlite3 .backup to a temp file (consistent, non-blocking)
#    2. Integrity-check the temp file (rejects corrupt copies)
#    3. Gzip + timestamp + checksum
#    4. Push to S3 if AWS_PROFILE or env vars are configured
#    5. Prune: keep 30 local, S3 lifecycle handles offsite retention
#
#  Offsite recommendations:
#    - AWS S3 with Object Lock + lifecycle to Glacier after 30 days
#    - OR rclone to any S3-compatible (Backblaze B2, Wasabi, etc)
#    - OR scp to a separate physical machine
#
#  Restore (manual):
#    gunzip < /opt/miracle-backups/miracle-YYYY-MM-DD-HHMMSS.db.gz \
#      > /tmp/restored.db
#    sqlite3 /tmp/restored.db "PRAGMA integrity_check;"
#    # If OK, stop service + replace:
#    sudo systemctl stop miracle-router
#    sudo cp /tmp/restored.db /etc/miracle-registry/miracle.db
#    sudo chown www-data:www-data /etc/miracle-registry/miracle.db   # match service user
#    sudo systemctl start miracle-router
# ══════════════════════════════════════════════════════════════

#  set -eo pipefail
#  
#  # ─── CONFIG ─────────────────────────────────────────────────────
#  DB_PATH="/etc/miracle-registry/miracle.db"
#  BACKUP_DIR="/opt/miracle-backups"
#  LOG_FILE="/var/log/miracle-backup.log"
#  KEEP_LOCAL=30
#  
#  # S3 / offsite -- leave S3_BUCKET empty to disable offsite push
#  S3_BUCKET="${MIRACLE_S3_BUCKET:-}"          # e.g. s3://my-miracle-backups
#  S3_PREFIX="${MIRACLE_S3_PREFIX:-gateway/}"  # path inside bucket
#  S3_STORAGE_CLASS="${MIRACLE_S3_CLASS:-STANDARD_IA}"  # cheaper than STANDARD
#  
#  # Flags
#  LOCAL_ONLY=false
#  VERIFY_ONLY=false
#  for arg in "$@"; do
#      case "$arg" in
#          --local-only) LOCAL_ONLY=true ;;
#          --verify)     VERIFY_ONLY=true ;;
#          --help|-h)
#              grep -E "^#" "$0" | head -40
#              exit 0
#              ;;
#      esac
#  done
#  
#  # ─── BOOTSTRAP ──────────────────────────────────────────────────
#  if [ "$EUID" -ne 0 ]; then
#      echo "ERROR: must run as root (sudo)." >&2
#      exit 1
#  fi
#  
#  mkdir -p "$BACKUP_DIR"
#  chmod 750 "$BACKUP_DIR"
#  touch "$LOG_FILE"
#  chmod 640 "$LOG_FILE"
#  
#  # ─── LOGGING ────────────────────────────────────────────────────
#  log() {
#      local ts
#      ts="$(date '+%Y-%m-%d %H:%M:%S')"
#      echo "[$ts] $1" | tee -a "$LOG_FILE"
#  }
#  
#  # ─── VERIFY MODE: integrity check the most recent backup ────────
#  if $VERIFY_ONLY; then
#      log "══════════════════════════════════════════"
#      log "VERIFY MODE: testing most recent backup"
#      log "══════════════════════════════════════════"
#  
#      LATEST=$(find "$BACKUP_DIR" -maxdepth 1 -name 'miracle-*.db.gz' -printf '%T@ %p\n' \
#          | sort -rn | head -1 | cut -d' ' -f2-)
#      if [ -z "$LATEST" ]; then
#          log "✗ No backups found in $BACKUP_DIR"
#          exit 1
#      fi
#      log "Latest: $LATEST"
#  
#      TMPRESTORE=$(mktemp /tmp/miracle-verify-XXXXXX.db)
#      trap 'rm -f "$TMPRESTORE"' EXIT
#  
#      gunzip -c "$LATEST" > "$TMPRESTORE"
#  
#      # Compare checksum to .sha256 sidecar (catches bitrot)
#      SHA_FILE="${LATEST}.sha256"
#      if [ -f "$SHA_FILE" ]; then
#          EXPECTED=$(awk '{print $1}' "$SHA_FILE")
#          ACTUAL=$(gunzip -c "$LATEST" | sha256sum | awk '{print $1}')
#          if [ "$EXPECTED" = "$ACTUAL" ]; then
#              log "✓ Checksum match"
#          else
#              log "✗ CHECKSUM MISMATCH: expected $EXPECTED, got $ACTUAL"
#              exit 1
#          fi
#      else
#          log "⚠ No .sha256 sidecar found (older backup format)"
#      fi
#  
#      # SQLite integrity check
#      INTEGRITY=$(sqlite3 "$TMPRESTORE" "PRAGMA integrity_check;")
#      if [ "$INTEGRITY" = "ok" ]; then
#          log "✓ SQLite integrity_check passed"
#      else
#          log "✗ SQLite integrity_check FAILED: $INTEGRITY"
#          exit 1
#      fi
#  
#      # Row counts -- sanity check that schema looks right
#      TABLES=$(sqlite3 "$TMPRESTORE" "SELECT name FROM sqlite_master WHERE type='table';")
#      log "Tables: $(echo "$TABLES" | tr '\n' ' ')"
#      for t in $TABLES; do
#          CNT=$(sqlite3 "$TMPRESTORE" "SELECT COUNT(*) FROM $t;")
#          log "  $t: $CNT rows"
#      done
#  
#      log "✅ Verify complete -- backup is restorable"
#      exit 0
#  fi
#  
#  # ─── BACKUP ─────────────────────────────────────────────────────
#  log "══════════════════════════════════════════"
#  log "Backup started"
#  log "══════════════════════════════════════════"
#  
#  if [ ! -f "$DB_PATH" ]; then
#      log "✗ DB file not found at $DB_PATH"
#      exit 1
#  fi
#  
#  TIMESTAMP=$(date '+%Y-%m-%d-%H%M%S')
#  TMP_BACKUP="$BACKUP_DIR/.miracle-$TIMESTAMP.db.tmp"
#  FINAL_BACKUP="$BACKUP_DIR/miracle-$TIMESTAMP.db.gz"
#  SHA_FILE="$FINAL_BACKUP.sha256"
#  
#  # 1. Online backup using SQLite's .backup command
#  #    This is safe against concurrent writes -- unlike `cp` on a live DB.
#  log "Creating consistent snapshot via sqlite3 .backup..."
#  sqlite3 "$DB_PATH" ".backup '$TMP_BACKUP'"
#  
#  # 2. Integrity-check the snapshot BEFORE compressing
#  log "Integrity check on snapshot..."
#  INTEGRITY=$(sqlite3 "$TMP_BACKUP" "PRAGMA integrity_check;")
#  if [ "$INTEGRITY" != "ok" ]; then
#      log "✗ Snapshot integrity check FAILED: $INTEGRITY"
#      rm -f "$TMP_BACKUP"
#      exit 1
#  fi
#  log "✓ Snapshot integrity OK"
#  
#  # 3. Compress + checksum
#  log "Compressing..."
#  gzip -c "$TMP_BACKUP" > "$FINAL_BACKUP"
#  FINAL_BASENAME="$(basename "$FINAL_BACKUP")"
#  sha256sum "$FINAL_BACKUP" | awk -v fn="$FINAL_BASENAME" '{print $1 "  " fn}' > "$SHA_FILE"
#  rm -f "$TMP_BACKUP"
#  
#  SIZE=$(du -h "$FINAL_BACKUP" | cut -f1)
#  log "✓ Local backup: $FINAL_BACKUP ($SIZE)"
#  
#  # 4. Push to S3 (if configured + not --local-only)
#  if [ -n "$S3_BUCKET" ] && ! $LOCAL_ONLY; then
#      if ! command -v aws >/dev/null 2>&1; then
#          log "⚠ aws CLI not installed -- skipping S3 push. Install with: apt install awscli"
#      else
#          log "Pushing to $S3_BUCKET/$S3_PREFIX..."
#          if aws s3 cp "$FINAL_BACKUP" "$S3_BUCKET/$S3_PREFIX$(basename "$FINAL_BACKUP")" \
#              --storage-class "$S3_STORAGE_CLASS" \
#              --no-progress 2>&1 | tee -a "$LOG_FILE"; then
#              aws s3 cp "$SHA_FILE" "$S3_BUCKET/$S3_PREFIX$(basename "$SHA_FILE")" \
#                  --storage-class "$S3_STORAGE_CLASS" \
#                  --no-progress 2>&1 | tee -a "$LOG_FILE"
#              log "✓ Pushed to S3"
#          else
#              log "✗ S3 push failed -- local copy is still safe"
#              # Don't fail the script -- local backup is the priority
#          fi
#      fi
#  elif $LOCAL_ONLY; then
#      log "  (--local-only set, skipping S3)"
#  else
#      log "  (S3_BUCKET not configured, skipping offsite push)"
#  fi
#  
#  # 5. Prune local backups -- keep last N
#  log "Pruning local backups (keep $KEEP_LOCAL)..."
#  PRUNED=$(find "$BACKUP_DIR" -maxdepth 1 -name 'miracle-*.db.gz' -printf '%T@ %p\n' \
#      | sort -rn \
#      | tail -n +$((KEEP_LOCAL + 1)) \
#      | cut -d' ' -f2-)
#  if [ -n "$PRUNED" ]; then
#      echo "$PRUNED" | while read -r old; do
#          rm -f "$old" "${old}.sha256"
#          log "  removed: $(basename "$old")"
#      done
#  else
#      log "  nothing to prune"
#  fi
#  
#  # 6. Sanity ping -- count rows in DB and log it (helps detect "DB shrank to 0 rows" anomalies)
#  USERS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM users;" 2>/dev/null || echo "?")
#  CLIENTS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM clients;" 2>/dev/null || echo "?")
#  SERVERS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM server_master;" 2>/dev/null || echo "?")
#  log "DB state: users=$USERS clients=$CLIENTS servers=$SERVERS"
#  
#  log "✅ Backup complete: $FINAL_BACKUP"
#  log "══════════════════════════════════════════"