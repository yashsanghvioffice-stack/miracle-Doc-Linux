#!/bin/bash
# ══════════════════════════════════════════════════════════════
#  MIRACLE CLOUD GATEWAY -- v4.3 migration runbook
#  users.start_date  ->  users.subscription_start
# ══════════════════════════════════════════════════════════════
#  Runs the whole pre-deploy sequence in order, with a backup
#  first and an explicit confirmation before anything is written.
#
#  Usage:
#    sudo ./migrate-v4_7.sh --check     # backup + inspect only, writes NOTHING
#    sudo ./migrate-v4_7.sh --dry-run   # + migration dry-run, still writes NOTHING
#    sudo ./migrate-v4_7.sh             # full run (prompts before the write)
#    sudo ./migrate-v4_7.sh --verify    # read-only: is the DB already migrated?
#
#  ORDER MATTERS: this must run BEFORE deploy.sh / init_db.py.
#  init_db.py only CREATEs and ADDs columns -- it cannot rename. On an
#  un-migrated DB it would ADD an EMPTY subscription_start beside the
#  populated start_date and every start date would read NULL. init_db.py
#  refuses (exit 3) in that state, but the clean path is to run this first.
#
#  Safety:
#    - Backs up the DB before touching anything
#    - Reports the users.user_type CHECK constraint (see STEP 2)
#    - Dry-run is shown and must be confirmed before the real write
#    - The migration itself is one transaction + self-verifies
#    - Does NOT deploy -- run deploy.sh yourself afterwards
# ══════════════════════════════════════════════════════════════

set -eo pipefail

# ─── CONFIG ─────────────────────────────────────────────────────
DB_PATH="${MIRACLE_DB_PATH:-/etc/miracle-registry/miracle.db}"
MIGRATION="/opt/miracle-router/migrations/v4_7_rename_start_date.py"
BACKUP_DIR="/opt/miracle-backups/v4_7"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/miracle.db.pre-v4_7.$STAMP"

MODE="full"
for arg in "$@"; do
    case "$arg" in
        --check)   MODE="check"   ;;
        --dry-run) MODE="dryrun"  ;;
        --verify)  MODE="verify"  ;;
        --help|-h) grep -E "^#" "$0" | head -28; exit 0 ;;
        *) echo "Unknown flag: $arg (try --help)"; exit 1 ;;
    esac
done

say()  { echo ""; echo "── $* ──"; }
die()  { echo ""; echo "ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root (sudo)."
[ -f "$DB_PATH" ]    || die "DB not found at $DB_PATH"
[ -f "$MIGRATION" ]  || die "migration not found at $MIGRATION -- deploy the code first, or run this from a checkout."

echo "══════════════════════════════════════════════════════════════"
echo "  v4.3 migration runbook   (mode: $MODE)"
echo "══════════════════════════════════════════════════════════════"
echo "  DB      : $DB_PATH"
echo "  Backup  : $BACKUP_FILE"

# ─── VERIFY-ONLY SHORT CIRCUIT ──────────────────────────────────
if [ "$MODE" = "verify" ]; then
    say "Read-only verification"
    python3 "$MIGRATION" --verify
    exit $?
fi

# ─── STEP 1: BACKUP ─────────────────────────────────────────────
say "STEP 1/5  Back up the database"
mkdir -p "$BACKUP_DIR"
chmod 750 "$BACKUP_DIR"
# .backup is safe on a live/WAL database; plain cp is not.
if sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'" 2>/dev/null; then
    echo "  sqlite3 .backup -> $BACKUP_FILE"
else
    cp "$DB_PATH" "$BACKUP_FILE"
    echo "  cp (sqlite3 CLI unavailable) -> $BACKUP_FILE"
fi
chmod 640 "$BACKUP_FILE"
echo "  size: $(du -h "$BACKUP_FILE" | cut -f1)"
echo "  RESTORE WITH:  sudo systemctl stop miracle-router && sudo cp $BACKUP_FILE $DB_PATH && sudo systemctl start miracle-router"

# ─── STEP 2: INSPECT THE user_type CHECK CONSTRAINT ─────────────
say "STEP 2/5  Inspect the users.user_type CHECK constraint"
if command -v sqlite3 >/dev/null 2>&1; then
    USERS_SQL="$(sqlite3 "$DB_PATH" "SELECT sql FROM sqlite_master WHERE name='users';")"
    echo "$USERS_SQL" | sed 's/^/  /'
    echo ""
    if echo "$USERS_SQL" | grep -q "CHECK(user_type IN ('new','additional'))"; then
        echo "  >> OLD 2-VALUE CHECK PRESENT."
        echo "     This DB will REJECT user_type='migrated' with an IntegrityError."
        echo "     SQLite cannot ALTER a CHECK -- the users table needs a rebuild."
        echo "     STOP and report this before continuing."
    elif echo "$USERS_SQL" | grep -q "user_type IN ('new','additional','migrated')"; then
        echo "  >> Widened 3-value CHECK already present. 'migrated' is accepted."
    else
        echo "  >> No CHECK on user_type (column was added via ALTER)."
        echo "     'migrated' is accepted; the enum is enforced at the BL layer."
    fi
else
    echo "  sqlite3 CLI not installed -- skipping. Check manually:"
    echo "    SELECT sql FROM sqlite_master WHERE name='users';"
fi

# ─── STEP 3: DRY RUN ────────────────────────────────────────────
say "STEP 3/5  Migration dry-run (writes nothing)"
python3 "$MIGRATION" --dry-run

if [ "$MODE" = "check" ] || [ "$MODE" = "dryrun" ]; then
    echo ""
    echo "══════════════════════════════════════════════════════════════"
    echo "  Stopped after the dry-run ($MODE). Nothing was written."
    echo "  Re-run without a flag to apply."
    echo "══════════════════════════════════════════════════════════════"
    exit 0
fi

# ─── STEP 4: APPLY (confirmed) ──────────────────────────────────
say "STEP 4/5  Apply the rename"
echo "  Review the dry-run above. This writes to the LIVE database."
printf "  Type 'yes' to apply: "
read -r CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "  Aborted. Nothing was written. Backup kept at $BACKUP_FILE"
    exit 1
fi
python3 "$MIGRATION"

# ─── STEP 5: VERIFY ─────────────────────────────────────────────
say "STEP 5/5  Verify"
python3 "$MIGRATION" --verify

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  v4_7 migration complete."
echo "  Backup: $BACKUP_FILE"
echo ""
echo "  NEXT -- deploy the code (init_db.py runs inside deploy.sh):"
echo "    sudo /opt/miracle-router/deploy.sh"
echo ""
echo "  Do NOT 'git pull' first -- deploy.sh pulls itself. A manual pull"
echo "  makes HEAD == origin, so deploy.sh reports 'Nothing to deploy'"
echo "  and skips the file copy, init_db.py and the service restart."
echo "══════════════════════════════════════════════════════════════"
