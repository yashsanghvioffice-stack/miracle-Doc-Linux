#!/bin/bash
# ══════════════════════════════════════════════════════════════
#  MIRACLE CLOUD GATEWAY -- Permission Normalizer
# ══════════════════════════════════════════════════════════════
#  Fixes ownership and permissions across the gateway's filesystem.
#  Idempotent: safe to run any time. Heals drift from manual chmods,
#  git pulls that reset perms, or partial deploys.
#
#  Usage:
#    sudo ./fix-permissions.sh           # apply fixes
#    sudo ./fix-permissions.sh --dry-run # show what would change, do nothing
#    sudo ./fix-permissions.sh --verify  # check current state, exit non-zero if wrong
#
#  When to run:
#    - After `git clone` on a fresh VM
#    - After `git pull` if anything feels off
#    - As the last step in deploy.sh (already integrated)
#    - When you see "Permission denied" / "CHDIR" / "EACCES" errors
#    - On a schedule, e.g. weekly cron, as a safety net
#
#  What it manages:
#    /opt/miracle-router/            -- Flask app + venv (service-owned, Python only)
#    /opt/miracle-scripts/           -- Operator .sh scripts (service-owned, executable)
#    /etc/miracle-registry/          -- SQLite DB (service-owned, restricted)
#    /var/www/miracle/               -- Static HTML (nginx-owned)
#    /etc/nginx/sites-available/...  -- nginx config (root, world-readable)
#    /etc/systemd/system/*.service   -- contains API key (root, 640)
#    /var/log/miracle-*.log          -- log files
#    /opt/miracle-backups/           -- DB backups (root, restricted)
#    /opt/miracle-backup/            -- deploy backups (root, restricted)
#
#  Service user (default: miracle) and nginx user (default: www-data) are
#  configurable below.
# ══════════════════════════════════════════════════════════════

set -eo pipefail

# ─── CONFIG ─────────────────────────────────────────────────────
SERVICE_USER="miracle"
SERVICE_GROUP="miracle"
NGINX_USER="www-data"
NGINX_GROUP="www-data"
LOG_FILE="/var/log/miracle-fixperms.log"

# Flags
DRY_RUN=false
VERIFY_ONLY=false
QUIET=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --verify)  VERIFY_ONLY=true ;;
        --quiet)   QUIET=true ;;
        --help|-h)
            grep -E "^#" "$0" | head -35
            exit 0
            ;;
    esac
done

# ─── BOOTSTRAP ──────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: must run as root (sudo)." >&2
    exit 1
fi

# Verify service user exists (skip in verify-only mode -- might be a sanity check itself)
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    echo "ERROR: service user '$SERVICE_USER' does not exist." >&2
    echo "       Create it with: sudo useradd --system --no-create-home --shell /usr/sbin/nologin $SERVICE_USER" >&2
    exit 1
fi

if ! getent group "$NGINX_GROUP" >/dev/null 2>&1; then
    echo "WARN: nginx group '$NGINX_GROUP' does not exist. nginx may not be installed." >&2
fi

# Log setup (skip if verify-only -- don't pollute log with read-only check)
if ! $VERIFY_ONLY; then
    touch "$LOG_FILE" 2>/dev/null || true
    chmod 640 "$LOG_FILE" 2>/dev/null || true
fi

# ─── LOGGING ────────────────────────────────────────────────────
log() {
    local ts msg
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    msg="[$ts] $1"
    if ! $QUIET; then
        echo "$msg"
    fi
    if ! $VERIFY_ONLY; then
        echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
    fi
}

# Track whether anything is wrong (for --verify exit code)
ISSUES_FOUND=0

# ─── HELPERS ────────────────────────────────────────────────────
#
# fix_path - the workhorse. Sets owner and mode on a single path.
#   $1: path
#   $2: expected owner:group
#   $3: expected mode (octal)
#   $4: type ("f" for file, "d" for dir, "any" for both)
#
fix_path() {
    local path="$1"
    local owner="$2"
    local mode="$3"
    local type="${4:-any}"

    if [ ! -e "$path" ]; then
        return 0  # silently skip missing paths -- not our job to create
    fi

    # Type check
    if [ "$type" = "f" ] && [ ! -f "$path" ]; then
        log "  ⚠ Expected file but found other type: $path"
        ISSUES_FOUND=$((ISSUES_FOUND + 1))
        return 0
    fi
    if [ "$type" = "d" ] && [ ! -d "$path" ]; then
        log "  ⚠ Expected directory but found other type: $path"
        ISSUES_FOUND=$((ISSUES_FOUND + 1))
        return 0
    fi

    # Check current state
    local current_owner current_mode
    current_owner="$(stat -c '%U:%G' "$path")"
    current_mode="$(stat -c '%a' "$path")"

    local needs_chown=false
    local needs_chmod=false
    [ "$current_owner" != "$owner" ] && needs_chown=true
    [ "$current_mode" != "$mode" ]  && needs_chmod=true

    if ! $needs_chown && ! $needs_chmod; then
        return 0  # already correct
    fi

    if $needs_chown; then
        if $DRY_RUN || $VERIFY_ONLY; then
            log "  [would chown] $path  ($current_owner -> $owner)"
        else
            chown "$owner" "$path"
            log "  ✓ chown $owner   $path"
        fi
        ISSUES_FOUND=$((ISSUES_FOUND + 1))
    fi

    if $needs_chmod; then
        if $DRY_RUN || $VERIFY_ONLY; then
            log "  [would chmod] $path  ($current_mode -> $mode)"
        else
            chmod "$mode" "$path"
            log "  ✓ chmod $mode    $path"
        fi
        ISSUES_FOUND=$((ISSUES_FOUND + 1))
    fi
}

#
# fix_tree - recursively normalize a directory tree.
#   $1: root path
#   $2: owner:group for everything inside
#   $3: directory mode (e.g. 755)
#   $4: file mode (e.g. 644)
#   $5: optional: file mode for executable files (e.g. 755, by name pattern)
#   $6: optional: glob pattern for executables (e.g. "*.sh")
#
fix_tree() {
    local root="$1"
    local owner="$2"
    local dir_mode="$3"
    local file_mode="$4"
    local exec_mode="${5:-}"
    local exec_glob="${6:-}"

    if [ ! -d "$root" ]; then
        return 0
    fi

    # Set perms on root itself
    fix_path "$root" "$owner" "$dir_mode" "d"

    # All subdirectories
    while IFS= read -r -d '' dir; do
        fix_path "$dir" "$owner" "$dir_mode" "d"
    done < <(find "$root" -mindepth 1 -type d -print0)

    # All regular files
    while IFS= read -r -d '' file; do
        # Decide which mode applies
        local target_mode="$file_mode"
        # shellcheck disable=SC2053  # intentional: RHS unquoted for glob matching
        if [ -n "$exec_glob" ] && [[ "$(basename "$file")" == $exec_glob ]]; then
            target_mode="$exec_mode"
        fi
        fix_path "$file" "$owner" "$target_mode" "f"
    done < <(find "$root" -type f -print0)
}

# ─── HEADER ─────────────────────────────────────────────────────
log "═══════════════════════════════════════════════════════"
if $VERIFY_ONLY; then
    log "Permission VERIFY mode (read-only)"
elif $DRY_RUN; then
    log "Permission DRY-RUN (will not apply changes)"
else
    log "Permission normalization starting"
fi
log "  Service user : $SERVICE_USER:$SERVICE_GROUP"
log "  Nginx user   : $NGINX_USER:$NGINX_GROUP"
log "═══════════════════════════════════════════════════════"

# ═══════════════════════════════════════════════════════════════
#  RULES
# ═══════════════════════════════════════════════════════════════

# ─── 1. Flask app directory (Python only) ──────────────────────
log "[1] /opt/miracle-router/  (Flask app + venv -- Python files only)"

# Root directory itself: 755 (must be enterable by service)
fix_path "/opt/miracle-router" "$SERVICE_USER:$SERVICE_GROUP" "755" "d"

# As of Phase 0 refactor (2026-05): .sh files live in /opt/miracle-scripts/
# (see next section). This dir holds router.py, init_db.py, migrate_*.py
# and the venv only. Any stray .sh here is legacy and still gets +x so it
# doesn't silently break, but new tooling should not put .sh files here.
if [ -d /opt/miracle-router ]; then
    # Top-level py/sh files
    while IFS= read -r -d '' file; do
        local_base="$(basename "$file")"
        if [[ "$local_base" == *.sh ]]; then
            # Legacy .sh files left behind by old deploys -- keep them
            # working but flag visually
            fix_path "$file" "$SERVICE_USER:$SERVICE_GROUP" "755" "f"
        else
            fix_path "$file" "$SERVICE_USER:$SERVICE_GROUP" "644" "f"
        fi
    done < <(find /opt/miracle-router -maxdepth 1 -type f -print0)

    # __pycache__ and other subdirs (not venv)
    while IFS= read -r -d '' dir; do
        if [ "$dir" != "/opt/miracle-router/venv" ]; then
            fix_path "$dir" "$SERVICE_USER:$SERVICE_GROUP" "755" "d"
        fi
    done < <(find /opt/miracle-router -mindepth 1 -maxdepth 1 -type d -print0)

    # venv: ownership only (don't mess with internal mode bits -- pip set them right)
    if [ -d /opt/miracle-router/venv ]; then
        local_current_owner="$(stat -c '%U:%G' /opt/miracle-router/venv)"
        if [ "$local_current_owner" != "$SERVICE_USER:$SERVICE_GROUP" ]; then
            if $DRY_RUN || $VERIFY_ONLY; then
                log "  [would chown -R] /opt/miracle-router/venv ($local_current_owner -> $SERVICE_USER:$SERVICE_GROUP)"
            else
                chown -R "$SERVICE_USER:$SERVICE_GROUP" /opt/miracle-router/venv
                log "  ✓ chown -R $SERVICE_USER:$SERVICE_GROUP   /opt/miracle-router/venv"
            fi
            ISSUES_FOUND=$((ISSUES_FOUND + 1))
        fi
    fi
fi

# ─── 2. Scripts directory (operator tooling) ────────────────────
log "[2] /opt/miracle-scripts/  (operator .sh scripts)"

# Dir 755 (must be enterable by anyone who runs the scripts under sudo)
fix_path "/opt/miracle-scripts" "$SERVICE_USER:$SERVICE_GROUP" "755" "d"

# All .sh files: 755 (executable). All other files: 644.
if [ -d /opt/miracle-scripts ]; then
    while IFS= read -r -d '' file; do
        local_base="$(basename "$file")"
        if [[ "$local_base" == *.sh ]]; then
            fix_path "$file" "$SERVICE_USER:$SERVICE_GROUP" "755" "f"
        else
            fix_path "$file" "$SERVICE_USER:$SERVICE_GROUP" "644" "f"
        fi
    done < <(find /opt/miracle-scripts -maxdepth 1 -type f -print0)
fi

# ─── 3. Database directory ──────────────────────────────────────
log "[3] /etc/miracle-registry/  (SQLite DB)"

fix_path "/etc/miracle-registry"              "$SERVICE_USER:$SERVICE_GROUP" "750" "d"
fix_path "/etc/miracle-registry/miracle.db"   "$SERVICE_USER:$SERVICE_GROUP" "640" "f"

# WAL companion files (created by SQLite when in WAL mode)
fix_path "/etc/miracle-registry/miracle.db-wal" "$SERVICE_USER:$SERVICE_GROUP" "640" "f"
fix_path "/etc/miracle-registry/miracle.db-shm" "$SERVICE_USER:$SERVICE_GROUP" "640" "f"

# ─── 4. Static web files ────────────────────────────────────────
log "[4] /var/www/miracle/  (HTML)"

if [ -d /var/www/miracle ] && getent group "$NGINX_GROUP" >/dev/null 2>&1; then
    fix_tree "/var/www/miracle" "$NGINX_USER:$NGINX_GROUP" "755" "644"
fi

# ─── 5. Nginx config ────────────────────────────────────────────
log "[5] /etc/nginx/sites-available/  (gateway config)"

fix_path "/etc/nginx/sites-available/miracle.cloud" "root:root" "644" "f"
# symlink itself doesn't get chmod'd; the target's perms are what matter

# ─── 6. Systemd unit (contains API key -- lock down) ────────────
log "[6] /etc/systemd/system/miracle-router.service"

fix_path "/etc/systemd/system/miracle-router.service" "root:root" "640" "f"

# ─── 7. Log files ───────────────────────────────────────────────
log "[7] /var/log/miracle-*.log"

# Router log is written by the service
fix_path "/var/log/miracle-router.log"     "$SERVICE_USER:$SERVICE_GROUP" "640" "f"

# Admin/operator logs -- written by root scripts
fix_path "/var/log/miracle-deploy.log"     "root:root" "640" "f"
fix_path "/var/log/miracle-backup.log"     "root:root" "640" "f"
fix_path "/var/log/miracle-backup-cron.log" "root:root" "640" "f"
fix_path "/var/log/miracle-keyrotate.log"  "root:root" "600" "f"   # contains key history
fix_path "/var/log/miracle-fixperms.log"   "root:root" "640" "f"

# ─── 8. Backup directories ──────────────────────────────────────
log "[8] /opt/miracle-backups/  (DB backups)"
log "    /opt/miracle-backup/   (deploy backups)"

# DB backups -- root only, contain potentially sensitive data
fix_path "/opt/miracle-backups"           "root:root" "750" "d"
fix_path "/opt/miracle-backups/keyrotate" "root:root" "700" "d"   # extra-restricted: old API keys

# Deploy backups -- root only
fix_path "/opt/miracle-backup"            "root:root" "750" "d"

# ─── 9. Git repo (production checkout) ──────────────────────────
log "[9] /opt/miracle-repo/  (git checkout)"

# Repo can stay root-owned (it's a deployment artifact, not a working dir).
# Just make sure scripts in the repo are executable so they can be run from there too.
if [ -d /opt/miracle-repo ]; then
    fix_path "/opt/miracle-repo" "root:root" "755" "d"

    # Scripts inside the repo should be executable
    while IFS= read -r -d '' script; do
        # Get current mode and check if executable bit is set for owner
        local_current_mode="$(stat -c '%a' "$script")"
        if [ "$local_current_mode" != "755" ] && [ "$local_current_mode" != "750" ]; then
            fix_path "$script" "root:root" "755" "f"
        fi
    done < <(find /opt/miracle-repo -type f \( -name "*.sh" -o -name "deploy.sh" \) -print0 2>/dev/null)
fi

# ═══════════════════════════════════════════════════════════════
#  REPORT
# ═══════════════════════════════════════════════════════════════
log "═══════════════════════════════════════════════════════"

if $VERIFY_ONLY; then
    if [ $ISSUES_FOUND -eq 0 ]; then
        log "✅ Verify complete -- all permissions correct"
        exit 0
    else
        log "❌ Verify FAILED -- $ISSUES_FOUND issue(s) found"
        log "   Run without --verify to fix."
        exit 1
    fi
elif $DRY_RUN; then
    if [ $ISSUES_FOUND -eq 0 ]; then
        log "✅ Dry-run complete -- nothing to fix"
    else
        log "ℹ Dry-run complete -- $ISSUES_FOUND change(s) would be made"
        log "  Run without --dry-run to apply."
    fi
    exit 0
else
    if [ $ISSUES_FOUND -eq 0 ]; then
        log "✅ All permissions already correct -- no changes needed"
    else
        log "✅ Fixed $ISSUES_FOUND permission issue(s)"
    fi
fi

log "═══════════════════════════════════════════════════════"
