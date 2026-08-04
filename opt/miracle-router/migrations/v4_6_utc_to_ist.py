#!/usr/bin/env python3
"""
v4_6 one-shot: convert existing UTC timestamps to IST (UTC+5:30).

Historical rows were written with SQLite CURRENT_TIMESTAMP / datetime('now'),
which are UTC. As of the IST change every NEW write is IST (config.SQL_NOW_IST);
this migration shifts every EXISTING timestamp column +5:30 so history matches.

Columns shifted (only where the table+column exist and the value is non-NULL):
    server_master        : created_at, updated_at
    users                : created_at, updated_at
    partners             : created_at, updated_at
    clients              : created_at
    rdp_download_tokens  : created_at, used_at
    request_log          : ts
NOT shifted: users.start_date, clients.subscription_start / subscription_end
(business DATES -- already IST, set from the desktop file / API, never UTC).

ONE-SHOT GUARD (PRAGMA user_version):
    0 = timestamps still UTC (original).   1 = already converted to IST.
The migration refuses to run twice (a second run would double-shift by +11h).
--dry-run reports without writing.

RUN ORDER: after init_db.py. Order vs v4_5 does not matter -- v4_5 reads
PRAGMA user_version and shifts created_at itself when this backfill hasn't run
yet -- but running v4_6 FIRST is the clean path.

SAFETY: one IMMEDIATE transaction (all-or-nothing); COALESCE keeps any
unparseable value unchanged rather than nulling it; integrity_check at the end.

Usage (root, after init_db.py):
    sudo python3 /opt/miracle-router/migrations/v4_6_utc_to_ist.py --dry-run
    sudo python3 /opt/miracle-router/migrations/v4_6_utc_to_ist.py
"""

import os
import sqlite3
import sys

DB_PATH = os.environ.get("MIRACLE_DB_PATH", "/etc/miracle-registry/miracle.db")

# UTC->IST offset as SQLite date-function modifiers. Hardcoded (NOT imported
# from config) on purpose: a migration is a historical snapshot and must keep
# doing exactly what it did when written, regardless of later config edits.
IST_SHIFT = "'+5 hours', '+30 minutes'"

# PRAGMA user_version sentinel: 1 == "all stored timestamps are IST".
USER_VERSION_IST = 1

# (table, column) timestamp columns to shift, in a stable order.
TARGETS = [
    ("server_master",       "created_at"),
    ("server_master",       "updated_at"),
    ("users",               "created_at"),
    ("users",               "updated_at"),
    ("partners",            "created_at"),
    ("partners",            "updated_at"),
    ("clients",             "created_at"),
    ("rdp_download_tokens", "created_at"),
    ("rdp_download_tokens", "used_at"),
    ("request_log",         "ts"),
]


def die(msg, code=1):
    """Print `msg` to stderr and exit with status `code`."""
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def table_exists(conn, table):
    """True if `table` is present in the DB."""
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def has_column(conn, table, col):
    """True if `table` currently has a column named `col`."""
    return any(r[1] == col for r in conn.execute("PRAGMA table_info(%s)" % table))


def main():
    """CLI entry: shift every existing timestamp column +5:30 (UTC->IST), once.
    Guarded by PRAGMA user_version; one IMMEDIATE txn; --dry-run previews."""
    dry = "--dry-run" in sys.argv
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        die("ERROR: must run as root (sudo).")
    if not os.path.exists(DB_PATH):
        die("ERROR: DB not found at %s" % DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        uv = conn.execute("PRAGMA user_version").fetchone()[0]
        print("=" * 66)
        print(" v4_6  UTC -> IST timestamp backfill")
        print(" mode         : %s" % ("DRY RUN" if dry else "APPLY"))
        print(" DB           : %s" % DB_PATH)
        print(" user_version : %d" % uv)
        print("=" * 66)

        if uv >= USER_VERSION_IST:
            print("Already converted to IST (user_version=%d). Nothing to do." % uv)
            print("=" * 66)
            return

        # Build the work list: existing table+column with non-NULL rows.
        work = []
        for tbl, col in TARGETS:
            if not table_exists(conn, tbl) or not has_column(conn, tbl, col):
                continue
            n = conn.execute(
                "SELECT COUNT(*) FROM %s WHERE %s IS NOT NULL" % (tbl, col)
            ).fetchone()[0]
            work.append((tbl, col, n))
            print("  %-22s.%-12s non-null rows: %d" % (tbl, col, n))

        if dry:
            for tbl, col, n in work:
                if n:
                    r = conn.execute(
                        "SELECT %s v FROM %s WHERE %s IS NOT NULL LIMIT 1" % (col, tbl, col)
                    ).fetchone()
                    after = conn.execute(
                        "SELECT datetime(?, %s)" % IST_SHIFT, (r["v"],)
                    ).fetchone()[0]
                    print("  e.g. %s.%s: %s (UTC) -> %s (IST)" % (tbl, col, r["v"], after))
                    break
            print("DRY RUN -- no changes written; user_version stays %d." % uv)
            print("=" * 66)
            return

        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        total = 0
        for tbl, col, n in work:
            cur = conn.execute(
                "UPDATE %s SET %s = COALESCE(datetime(%s, %s), %s) WHERE %s IS NOT NULL"
                % (tbl, col, col, IST_SHIFT, col, col))
            total += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.execute("PRAGMA user_version = %d" % USER_VERSION_IST)
        conn.execute("COMMIT")

        print("--- DONE ---")
        print("  rows shifted (approx) : %d" % total)
        print("  user_version now      : %d"
              % conn.execute("PRAGMA user_version").fetchone()[0])
        integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
        print("  integrity_check       : %s" % integ)
        print("=" * 66)
        print(" UTC -> IST backfill %s" % ("OK" if integ == "ok" else "FAILED"))
        print("=" * 66)
        if integ != "ok":
            sys.exit(3)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
