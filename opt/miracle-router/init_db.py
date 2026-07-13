#!/usr/bin/env python3
"""
Miracle Cloud Gateway -- DB Bootstrap & Schema Sync

ONE idempotent script for both fresh installs and existing DBs:

    * Tables missing  -> CREATE TABLE IF NOT EXISTS
    * Columns missing -> ALTER TABLE ADD COLUMN
    * Indexes missing -> CREATE INDEX IF NOT EXISTS

Safe to run on every deploy. Does NOT drop or rename anything, never
touches existing rows, never re-runs CREATE on a present table.

Run on the gateway VM as root:
    sudo python3 /opt/miracle-router/init_db.py

Flags:
    --dry-run     Report what would change, write nothing
    --verify      Read-only check; non-zero exit if drift found
    --help        Show this message

Adding a column later:
    1. Edit SCHEMA below: add the column to `create` (for fresh installs)
       AND to `add_columns` (so it's also applied on existing DBs).
    2. Run this script on each deployed gateway.

SQLite ALTER TABLE ADD COLUMN restrictions (apply to `add_columns`):
    * No PRIMARY KEY, no UNIQUE.
    * No NOT NULL without an explicit DEFAULT.
    * Default cannot be CURRENT_TIMESTAMP / CURRENT_TIME / CURRENT_DATE.
If a future column hits these limits, you must write a one-off
ALTER+UPDATE block manually -- this script will not silently break.


"""

import os
import sqlite3
import sys

DB_PATH = os.environ.get("MIRACLE_DB_PATH", "/etc/miracle-registry/miracle.db")
DB_DIR  = os.path.dirname(DB_PATH)


# ─── SCHEMA ─────────────────────────────────────────────────────
# Each entry is a dict with:
#   table        - SQL identifier
#   create       - full CREATE TABLE (used on fresh installs)
#   add_columns  - {col_name: ALTER-safe definition} -- applied if column
#                  is missing on an existing table. Omit columns that
#                  cannot be safely ADDed (PRIMARY KEY, UNIQUE, etc.).
#   indexes      - list of CREATE INDEX IF NOT EXISTS statements

SCHEMA = [
    {
        "table": "server_master",
        "create": """
            CREATE TABLE IF NOT EXISTS server_master (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                server_name  TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                server_ip    TEXT    NOT NULL UNIQUE,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at   TIMESTAMP
            )
        """,
        "add_columns": {
            "updated_at": "TIMESTAMP",
        },
        "indexes": [],
    },
    {
        "table": "users",
        "create": """
            CREATE TABLE IF NOT EXISTS users (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                username     TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                client_name  TEXT    NOT NULL,
                email        TEXT    NOT NULL,
                mobile       TEXT    NOT NULL,
                server_id    INTEGER NOT NULL,
                is_active    INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
                user_type    TEXT    NOT NULL DEFAULT 'new' CHECK(user_type IN ('new','additional')),
                start_date   DATE,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at   TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES server_master(id)
            )
        """,
        "add_columns": {
            "email":      "TEXT NOT NULL DEFAULT ''",
            "mobile":     "TEXT NOT NULL DEFAULT ''",
            "is_active":  "INTEGER NOT NULL DEFAULT 1",
            # user_type added in Phase 2. NOT NULL DEFAULT 'new' means the
            # ALTER auto-fills every existing row with 'new' -- so the
            # v4_3_backfill_users.py migration is a safety no-op. CHECK is
            # omitted here (ADD COLUMN keeps it simple; the enum is
            # enforced at the BL layer). Fresh DBs get the CHECK via CREATE.
            "user_type":  "TEXT NOT NULL DEFAULT 'new'",
            # start_date (v4.1a) = per-user subscription/purchase start
            # (= creation date). Nullable; existing rows backfilled to
            # date(created_at) by migrations/v4_4_backfill_user_start.py.
            "start_date": "DATE",
            "updated_at": "TIMESTAMP",
        },
        "indexes": [
            "CREATE INDEX IF NOT EXISTS idx_users_client_name ON users(client_name COLLATE NOCASE)",
            "CREATE INDEX IF NOT EXISTS idx_users_server_id   ON users(server_id)",
            "CREATE INDEX IF NOT EXISTS idx_users_is_active   ON users(is_active)",
        ],
    },
    {
        "table": "rdp_download_tokens",
        "create": """
            CREATE TABLE IF NOT EXISTS rdp_download_tokens (
                token        TEXT    PRIMARY KEY,
                username     TEXT    NOT NULL,
                server_ip    TEXT    NOT NULL,
                created_at   TIMESTAMP NOT NULL,
                used_at      TIMESTAMP
            )
        """,
        "add_columns": {
            "used_at": "TIMESTAMP",
        },
        "indexes": [
            "CREATE INDEX IF NOT EXISTS idx_rdp_tokens_created ON rdp_download_tokens(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_rdp_tokens_used    ON rdp_download_tokens(used_at)",
        ],
    },
    {
        "table": "partners",
        "create": """
            CREATE TABLE IF NOT EXISTS partners (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                email        TEXT,
                phone        TEXT,
                is_active    INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at   TIMESTAMP
            )
        """,
        "add_columns": {
            "email":      "TEXT",
            "phone":      "TEXT",
            "is_active":  "INTEGER NOT NULL DEFAULT 1",
            "updated_at": "TIMESTAMP",
        },
        "indexes": [
            "CREATE INDEX IF NOT EXISTS idx_partners_is_active ON partners(is_active)",
        ],
    },
    {
        "table": "clients",
        "create": """
            CREATE TABLE IF NOT EXISTS clients (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name        TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                display_name       TEXT,
                ukey               TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                partner_id         INTEGER,
                subscription_type  TEXT,
                storage_gb         INTEGER,
                subscription_start DATE,
                subscription_end   DATE,
                contact_email      TEXT,
                contact_mobile     TEXT,
                created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CHECK(length(ukey) = 8)
            )
        """,
        "add_columns": {
            "display_name": "TEXT",
            # partner_id added in Phase 1 so partners_dal can count
            # referencing clients. Phase 2 activates it (writable via
            # POST/PUT) and adds the subscription date columns. All three
            # are nullable -- existing rows are populated by the one-shot
            # migrations/v4_2_backfill_clients.py (SQLite ADD COLUMN can't
            # take a date(created_at) default expression).
            "partner_id":         "INTEGER",
            # subscription_type ('single'|'multi') + storage_gb (total shared
            # HARD quota) added in v4.1a. Nullable; enum enforced at BL. No
            # backfill -- existing clients edited later via PUT.
            "subscription_type":  "TEXT",
            "storage_gb":         "INTEGER",
            # subscription_start is DEPRECATED in v4.1 (start moved to
            # users.start_date). Column kept for compatibility; no longer
            # read or written. subscription_end (expiry) stays authoritative.
            "subscription_start": "DATE",
            "subscription_end":   "DATE",
            # Account-level customer contact (v4.1 migration). Nullable;
            # distinct from per-user users.email/mobile. Seeded from the
            # admin user by migrations/v4_5_seed_partners_contacts.py.
            "contact_email":      "TEXT",
            "contact_mobile":     "TEXT",
        },
        "indexes": [
            "CREATE INDEX IF NOT EXISTS idx_clients_ukey       ON clients(ukey COLLATE NOCASE)",
            "CREATE INDEX IF NOT EXISTS idx_clients_partner_id ON clients(partner_id)",
        ],
    },
    {
        "table": "request_log",
        "create": """
            CREATE TABLE IF NOT EXISTS request_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                method        TEXT NOT NULL,
                path          TEXT NOT NULL,
                status        INTEGER,
                duration_ms   INTEGER,
                client_ip     TEXT,
                username      TEXT,
                ukey          TEXT,
                error_code    TEXT,
                exception     TEXT
            )
        """,
        "add_columns": {
            "method":      "TEXT NOT NULL DEFAULT ''",
            "path":        "TEXT NOT NULL DEFAULT ''",
            "status":      "INTEGER",
            "duration_ms": "INTEGER",
            "client_ip":   "TEXT",
            "username":    "TEXT",
            "ukey":        "TEXT",
            "error_code":  "TEXT",
            "exception":   "TEXT",
        },
        "indexes": [
            "CREATE INDEX IF NOT EXISTS idx_request_log_ts       ON request_log(ts)",
            "CREATE INDEX IF NOT EXISTS idx_request_log_username ON request_log(username)",
            "CREATE INDEX IF NOT EXISTS idx_request_log_status   ON request_log(status)",
        ],
    },
]


# ─── HELPERS ────────────────────────────────────────────────────

def list_tables(conn):
    return {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def list_indexes(conn):
    return {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def list_columns(conn, table):
    """Return set of column names currently on `table`."""
    rows = conn.execute("PRAGMA table_info(%s)" % table).fetchall()
    return {r[1] for r in rows}


def row_count(conn, table):
    try:
        return conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
    except sqlite3.Error:
        return "?"


def index_name(stmt):
    """Pull the index name out of a 'CREATE INDEX IF NOT EXISTS <name> ...' string."""
    parts = stmt.split()
    try:
        return parts[parts.index("EXISTS") + 1]
    except (ValueError, IndexError):
        return "(unknown)"


# ─── MAIN ───────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv
    verify  = "--verify"  in sys.argv

    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    if os.geteuid() != 0:
        sys.stderr.write("ERROR: must run as root (sudo).\n")
        sys.exit(1)

    if not os.path.exists(DB_DIR):
        if dry_run or verify:
            print("WOULD CREATE: %s" % DB_DIR)
        else:
            os.makedirs(DB_DIR, exist_ok=True)
            os.chmod(DB_DIR, 0o750)
            print("Created: %s" % DB_DIR)

    fresh_db = not os.path.exists(DB_PATH)
    if fresh_db and verify:
        sys.stderr.write("ERROR: --verify requested but DB doesn't exist at %s\n" % DB_PATH)
        sys.exit(1)

    print("=" * 60)
    if verify:
        print(" Miracle DB -- VERIFY (read-only)")
    elif dry_run:
        print(" Miracle DB -- DRY RUN (no changes)")
    else:
        print(" Miracle DB -- bootstrap / sync")
    print("=" * 60)
    print("  DB path : %s" % DB_PATH)
    print("  Fresh   : %s" % ("yes" if fresh_db else "no -- syncing schema"))
    print()

    drift = False  # used by --verify to set non-zero exit

    conn = sqlite3.connect(DB_PATH)
    try:
        if not (dry_run or verify):
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")

        existing_tables  = list_tables(conn)
        existing_indexes = list_indexes(conn)

        for entry in SCHEMA:
            table  = entry["table"]
            create = entry["create"]
            addcols = entry.get("add_columns", {})
            indexes = entry.get("indexes", [])

            # ── Table ──
            table_preexisted = table in existing_tables
            if not table_preexisted:
                drift = True
                if verify:
                    print("  [MISSING TABLE] %s" % table)
                elif dry_run:
                    print("  [WOULD CREATE] %s" % table)
                else:
                    conn.execute(create)
                    print("  [CREATED] %s" % table)
            else:
                cnt = row_count(conn, table)
                print("  [OK] %-22s exists (%s rows)" % (table, cnt))

            # ── Columns (ALTER TABLE ADD COLUMN for missing) ──
            # Only meaningful for tables that pre-existed: a freshly-created
            # table already has every column from `create`.
            current_cols = list_columns(conn, table) if table_preexisted or not (dry_run or verify) else set()
            for col_name, col_def in addcols.items():
                if not table_preexisted:
                    continue  # nothing to ALTER on a brand-new table
                if col_name in current_cols:
                    continue
                drift = True
                stmt = "ALTER TABLE %s ADD COLUMN %s %s" % (table, col_name, col_def)
                if verify:
                    print("         · [MISSING COLUMN] %s.%s" % (table, col_name))
                elif dry_run:
                    print("         · [WOULD ADD COLUMN] %s.%s %s" % (table, col_name, col_def))
                else:
                    try:
                        conn.execute(stmt)
                        print("         · added column %s.%s" % (table, col_name))
                    except sqlite3.Error as e:
                        sys.stderr.write(
                            "         · FAILED to add %s.%s: %s\n"
                            "           Stmt: %s\n"
                            % (table, col_name, e, stmt)
                        )
                        raise

            # ── Indexes ──
            for idx_stmt in indexes:
                idx_n = index_name(idx_stmt)
                if idx_n in existing_indexes:
                    continue
                drift = True
                if verify:
                    print("         · [MISSING INDEX] %s" % idx_n)
                elif dry_run:
                    print("         · [WOULD CREATE INDEX] %s" % idx_n)
                else:
                    conn.execute(idx_stmt)
                    print("         · created index %s" % idx_n)

        if not (dry_run or verify):
            conn.commit()

        # ── Final summary ──
        print()
        print("Final state:")
        final_tables = list_tables(conn)
        for entry in SCHEMA:
            t = entry["table"]
            present = t in final_tables
            cnt = row_count(conn, t) if present else "—"
            mark = "OK" if present else "MISSING"
            print("  %-22s %-8s (%s rows)" % (t, mark, cnt))

    finally:
        conn.close()

    if not (dry_run or verify) and os.path.exists(DB_PATH):
        try:
            os.chmod(DB_PATH, 0o640)
        except Exception as e:
            print("WARN: could not chmod %s: %s" % (DB_PATH, e))

    print()
    print("=" * 60)
    if verify:
        if drift:
            print(" Verify: DRIFT DETECTED (see [MISSING ...] above)")
            sys.exit(2)
        print(" Verify: schema matches")
    elif dry_run:
        print(" Dry run complete -- no changes made")
    else:
        print(" Bootstrap / sync complete")
        if fresh_db:
            print()
            print(" Next:")
            print("   sudo chown miracle:miracle %s" % DB_PATH)
            print("   sudo systemctl restart miracle-router")
            print("   curl -s http://127.0.0.1:5001/health")
    print("=" * 60)


if __name__ == "__main__":
    main()
