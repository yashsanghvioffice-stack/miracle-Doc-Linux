```bash
#!/bin/bash
# ══════════════════════════════════════════════════════════════
#  MIRACLE CLOUD GATEWAY -- API Key Rotation
# ══════════════════════════════════════════════════════════════
#  Rotates MIRACLE_API_KEY in the systemd unit file, restarts
#  the router, verifies the new key works, and prints out the
#  new key for distribution to the Windows TSplus server(s).
#
#  Usage:
#    sudo ./rotate-api-key.sh           # generate + apply new key
#    sudo ./rotate-api-key.sh --dry-run # show new key, don't apply
#    sudo ./rotate-api-key.sh --show    # show current key only
#
#  After rotation, on EACH Windows TSplus server (PowerShell as admin):
#    [Environment]::SetEnvironmentVariable("MIRACLE_GATEWAY_API_KEY", "<new-key>", "Machine")
#    # Then open a NEW PowerShell session -- machine env vars don't
#    # propagate to existing sessions.
#
#  Safety:
#    - Backs up the systemd unit file before editing
#    - Verifies the new key by calling /admin/stats with it
#    - If verification fails, restores backup + restarts service
#    - Logs everything to /var/log/miracle-keyrotate.log
# ══════════════════════════════════════════════════════════════

set -eo pipefail

# ─── CONFIG ─────────────────────────────────────────────────────
SERVICE_FILE="/etc/systemd/system/miracle-router.service"
BACKUP_DIR="/opt/miracle-backups/keyrotate"
LOG_FILE="/var/log/miracle-keyrotate.log"
ROUTER_URL="http://127.0.0.1:5001"

# Flags
DRY_RUN=false
SHOW_ONLY=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --show)    SHOW_ONLY=true ;;
        --help|-h)
            grep -E "^#" "$0" | head -25
            exit 0
            ;;
    esac
done

# ─── BOOTSTRAP ──────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: must run as root (sudo)." >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
touch "$LOG_FILE"
chmod 600 "$LOG_FILE"   # contains old + new keys, lock it down

# ─── LOGGING ────────────────────────────────────────────────────
log() {
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[$ts] $1" | tee -a "$LOG_FILE"
}

# Don't log key values to stdout, only to file
log_secret() {
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[$ts] $1" >> "$LOG_FILE"
}

# ─── SANITY CHECKS ──────────────────────────────────────────────
if [ ! -f "$SERVICE_FILE" ]; then
    log "✗ Service file not found: $SERVICE_FILE"
    exit 1
fi

# Extract current key from the unit file
CURRENT_KEY=$(grep -E '^Environment="MIRACLE_API_KEY=' "$SERVICE_FILE" \
    | sed -E 's/^Environment="MIRACLE_API_KEY=(.+)"$/\1/' || true)

if [ -z "$CURRENT_KEY" ]; then
    log "✗ Could not parse current MIRACLE_API_KEY from $SERVICE_FILE"
    log "  Expected line format: Environment=\"MIRACLE_API_KEY=...\""
    exit 1
fi

# ─── SHOW-ONLY MODE ─────────────────────────────────────────────
if $SHOW_ONLY; then
    echo ""
    echo "Current MIRACLE_API_KEY:"
    echo "  $CURRENT_KEY"
    echo ""
    echo "Length: ${#CURRENT_KEY} chars"
    if [ "$CURRENT_KEY" = "CHANGE_ME_TO_A_STRONG_SECRET_KEY" ]; then
        echo ""
        echo "⚠️  WARNING: this is the placeholder key. Rotate immediately."
    fi
    exit 0
fi

# ─── GENERATE NEW KEY ───────────────────────────────────────────
# 32 bytes hex = 64 chars. Plenty of entropy, fits cleanly in headers.
NEW_KEY=$(openssl rand -hex 32)

log "══════════════════════════════════════════"
log "API Key Rotation Starting"
log "══════════════════════════════════════════"
log_secret "OLD key: $CURRENT_KEY"
log_secret "NEW key: $NEW_KEY"
log "Current key length: ${#CURRENT_KEY} chars"
log "New key length: ${#NEW_KEY} chars"

if $DRY_RUN; then
    log "DRY-RUN: would replace key but not applying"
    echo ""
    echo "═══════════════════════════════════════════════════════"
    echo " DRY-RUN -- new key would be:"
    echo "═══════════════════════════════════════════════════════"
    echo ""
    echo "  $NEW_KEY"
    echo ""
    echo " (not applied -- re-run without --dry-run to apply)"
    exit 0
fi

# ─── BACKUP SYSTEMD UNIT ────────────────────────────────────────
TIMESTAMP=$(date '+%Y-%m-%d-%H%M%S')
BACKUP_FILE="$BACKUP_DIR/miracle-router.service.$TIMESTAMP.bak"
cp "$SERVICE_FILE" "$BACKUP_FILE"
chmod 600 "$BACKUP_FILE"
log "✓ Backed up unit file: $BACKUP_FILE"

# ─── ROLLBACK FUNCTION ──────────────────────────────────────────
rollback_key() {
    log "❌ Verification failed -- restoring previous key"
    cp "$BACKUP_FILE" "$SERVICE_FILE"
    systemctl daemon-reload
    systemctl restart miracle-router

    sleep 3
    if curl -s --max-time 5 "$ROUTER_URL/health" | grep -q '"status":"ok"'; then
        log "✓ Old key restored, router is back up"
    else
        log "🚨 CRITICAL: router did NOT come back after rollback. Manual fix needed."
    fi
    exit 1
}

# ─── APPLY NEW KEY ──────────────────────────────────────────────
# Use a different delimiter for sed because the key could theoretically
# contain forward slashes (openssl hex won't, but defensive).
log "Updating $SERVICE_FILE..."
# shellcheck disable=SC1003
sed -i.tmp "s|^Environment=\"MIRACLE_API_KEY=.*\"|Environment=\"MIRACLE_API_KEY=$NEW_KEY\"|" "$SERVICE_FILE"
rm -f "${SERVICE_FILE}.tmp"

# Verify the substitution actually happened
APPLIED=$(grep -E '^Environment="MIRACLE_API_KEY=' "$SERVICE_FILE" \
    | sed -E 's/^Environment="MIRACLE_API_KEY=(.+)"$/\1/')
if [ "$APPLIED" != "$NEW_KEY" ]; then
    log "✗ sed substitution failed. Restoring backup."
    cp "$BACKUP_FILE" "$SERVICE_FILE"
    exit 1
fi
log "✓ Unit file updated"

# ─── RELOAD + RESTART ───────────────────────────────────────────
log "systemctl daemon-reload..."
systemctl daemon-reload

log "Restarting miracle-router..."
systemctl restart miracle-router

# Give it a moment to come up
sleep 3

# ─── VERIFY NEW KEY WORKS ───────────────────────────────────────
log "Verifying new key against /admin/stats..."

# Try up to 5 times -- the service might still be starting
VERIFIED=false
for attempt in 1 2 3 4 5; do
    RESP=$(curl -s --max-time 5 -H "X-API-Key: $NEW_KEY" "$ROUTER_URL/admin/stats" || echo "")
    if echo "$RESP" | grep -q '"total_users"'; then
        log "✓ New key accepted by router (attempt $attempt)"
        VERIFIED=true
        break
    fi
    log "  attempt $attempt failed, retrying in 2s..."
    sleep 2
done

if ! $VERIFIED; then
    log "✗ Could not verify new key after 5 attempts"
    rollback_key
fi

# Confirm the OLD key no longer works (sanity check, not strictly required)
OLD_RESP=$(curl -s --max-time 5 -H "X-API-Key: $CURRENT_KEY" "$ROUTER_URL/admin/stats" || echo "")
if echo "$OLD_RESP" | grep -q '"total_users"'; then
    log "🚨 WARNING: OLD key still works -- something is wrong with the rotation"
    rollback_key
else
    log "✓ Old key correctly rejected"
fi

# ─── SUCCESS OUTPUT ─────────────────────────────────────────────
log "✅ Rotation complete"
log "══════════════════════════════════════════"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo " ✅ API KEY ROTATED SUCCESSFULLY"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo " New API Key:"
echo ""
echo "   $NEW_KEY"
echo ""
echo " Update EACH Windows TSplus server (PowerShell as admin):"
echo ""
echo "   [Environment]::SetEnvironmentVariable("
echo "       'MIRACLE_GATEWAY_API_KEY',"
echo "       '$NEW_KEY',"
echo "       'Machine')"
echo ""
echo " Then open a NEW PowerShell session on those servers."
echo " (Machine env vars don't propagate to existing sessions.)"
echo ""
echo " Old unit file backed up to:"
echo "   $BACKUP_FILE"
echo ""
echo " Rollback if needed:"
echo "   sudo cp $BACKUP_FILE $SERVICE_FILE"
echo "   sudo systemctl daemon-reload && sudo systemctl restart miracle-router"
echo ""
echo "═══════════════════════════════════════════════════════════════"
```