#!/usr/bin/env python3
"""
Miracle Cloud Gateway -- DB Bootstrap

Initialize ALL required tables in one shot. Idempotent: safe to run
on a fresh VM OR an existing DB. Tables that already exist are left
alone; missing tables are created.

Run on the gateway VM as root:
    sudo python3 /opt/miracle-router/init_all_db.py

What it creates (if missing):
    server_master         -- TSplus server registry
    users                 -- per-user accounts (email, mobile, is_active, server_id)
    rdp_download_tokens   -- single-use .rdp file download tokens
    clients               -- uKey table (one row per tenant)

What it does NOT do:
    - Drop or alter existing tables (this is safe-by-default)
    - Insert any seed data
    - Touch existing rows

If you need a destructive reset, do it manually:
    sudo systemctl stop miracle-router
    sudo rm /etc/miracle-registry/miracle.db
    sudo python3 /opt/miracle-router/init_all_db.py
    sudo systemctl start miracle-router

Flags:
    --dry-run     Report what would change without writing
    --verify      Just check current schema, don't modify anything
"""

import os
import sqlite3
import sys

DB_PATH = os.environ.get("MIRACLE_DB_PATH", "/etc/miracle-registry/miracle.db")
DB_DIR  = os.path.dirname(DB_PATH)

# ─── SCHEMA DEFINITIONS ─────────────────────────────────────────
# Each entry: (table_name, CREATE statement, [extra index/trigger statements])
# All CREATE statements use IF NOT EXISTS so re-running is safe.

SCHEMA = [
    (
        "server_master",
        """
        CREATE TABLE IF NOT EXISTS server_master (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            server_name  TEXT    NOT NULL UNIQUE COLLATE NOCASE,
            server_ip    TEXT    NOT NULL UNIQUE,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at   TIMESTAMP
        )
        """,
        [],
    ),
    (
        "users",
        """
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT    NOT NULL UNIQUE COLLATE NOCASE,
            client_name  TEXT    NOT NULL,
            email        TEXT    NOT NULL,
            mobile       TEXT    NOT NULL,
            server_id    INTEGER NOT NULL,
            is_active    INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at   TIMESTAMP,
            FOREIGN KEY (server_id) REFERENCES server_master(id)
        )
        """,
        [
            "CREATE INDEX IF NOT EXISTS idx_users_client_name ON users(client_name COLLATE NOCASE)",
            "CREATE INDEX IF NOT EXISTS idx_users_server_id   ON users(server_id)",
            "CREATE INDEX IF NOT EXISTS idx_users_is_active   ON users(is_active)",
        ],
    ),
    (
        "rdp_download_tokens",
        """
        CREATE TABLE IF NOT EXISTS rdp_download_tokens (
            token        TEXT    PRIMARY KEY,
            username     TEXT    NOT NULL,
            server_ip    TEXT    NOT NULL,
            created_at   TIMESTAMP NOT NULL,
            used_at      TIMESTAMP
        )
        """,
        [
            "CREATE INDEX IF NOT EXISTS idx_rdp_tokens_created ON rdp_download_tokens(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_rdp_tokens_used    ON rdp_download_tokens(used_at)",
        ],
    ),
    (
        "clients",
        """
        CREATE TABLE IF NOT EXISTS clients (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            client_name  TEXT    NOT NULL UNIQUE COLLATE NOCASE,
            ukey         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            CHECK(length(ukey) = 8)
        )
        """,
        [
            "CREATE INDEX IF NOT EXISTS idx_clients_ukey ON clients(ukey COLLATE NOCASE)",
        ],
    ),
]


# ─── HELPERS ────────────────────────────────────────────────────

def list_existing_tables(conn):
    return {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def list_existing_indexes(conn):
    return {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def row_count(conn, table):
    try:
        return conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
    except sqlite3.Error:
        return "?"


# ─── MAIN ───────────────────────────────────────────────────────

def main():
    # Parse flags
    dry_run = "--dry-run" in sys.argv
    verify  = "--verify"  in sys.argv

    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    # Must be root
    if os.geteuid() != 0:
        sys.stderr.write("ERROR: must run as root (sudo).\n")
        sys.exit(1)

    # Create directory if missing
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
        print(" Miracle DB Bootstrap -- VERIFY mode (read-only)")
    elif dry_run:
        print(" Miracle DB Bootstrap -- DRY RUN (no changes)")
    else:
        print(" Miracle DB Bootstrap")
    print("=" * 60)
    print("  DB path : %s" % DB_PATH)
    print("  Fresh   : %s" % ("yes -- new DB will be created" if fresh_db else "no -- existing DB"))
    print()

    conn = sqlite3.connect(DB_PATH)
    try:
        # Always apply pragmas (cheap, idempotent)
        if not (dry_run or verify):
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")

        existing_tables  = list_existing_tables(conn)
        existing_indexes = list_existing_indexes(conn)

        # ── Apply schema ──
        for table_name, create_stmt, extras in SCHEMA:
            if table_name in existing_tables:
                cnt = row_count(conn, table_name) if not dry_run else row_count(conn, table_name)
                print("  [SKIP] %-22s already exists (%s rows)" % (table_name, cnt))
            else:
                if verify:
                    print("  [MISSING] %s" % table_name)
                elif dry_run:
                    print("  [WOULD CREATE] %s" % table_name)
                else:
                    conn.execute(create_stmt)
                    print("  [CREATED] %s" % table_name)

            # Indexes for this table
            for idx_stmt in extras:
                # Parse the index name from "CREATE INDEX IF NOT EXISTS <name> ON ..."
                parts = idx_stmt.split()
                try:
                    idx_name = parts[parts.index("EXISTS") + 1]
                except (ValueError, IndexError):
                    idx_name = "(unknown)"

                if idx_name in existing_indexes:
                    print("         · index %s already present" % idx_name)
                else:
                    if verify:
                        print("         · [MISSING INDEX] %s" % idx_name)
                    elif dry_run:
                        print("         · [WOULD CREATE INDEX] %s" % idx_name)
                    else:
                        conn.execute(idx_stmt)
                        print("         · created index %s" % idx_name)

        if not (dry_run or verify):
            conn.commit()

        # ── Final report ──
        print()
        print("Final state:")
        final_tables  = list_existing_tables(conn)
        for table_name, _, _ in SCHEMA:
            present = table_name in final_tables
            cnt     = row_count(conn, table_name) if present else "—"
            status  = "✓" if present else "✗ MISSING"
            print("  %-22s %s  (%s rows)" % (table_name, status, cnt))

    finally:
        conn.close()

    # Lock down DB file permissions (unless dry-run/verify)
    if not (dry_run or verify) and os.path.exists(DB_PATH):
        try:
            os.chmod(DB_PATH, 0o640)
        except Exception as e:
            print("WARN: could not chmod %s: %s" % (DB_PATH, e))

    print()
    print("=" * 60)
    if verify:
        print(" Verify complete -- no changes made")
    elif dry_run:
        print(" Dry run complete -- no changes made")
    else:
        print(" Bootstrap complete")
        print()
        print(" Next: chown to the service user, then restart router")
        print("   sudo chown miracle:miracle %s" % DB_PATH)
        print("   sudo systemctl restart miracle-router")
        print("   curl -s http://127.0.0.1:5001/health")
    print("=" * 60)


if __name__ == "__main__":
    main()