#!/usr/bin/env python3
"""
Phase 2 data backfill -- users.user_type.

In practice this is a SAFETY NO-OP. init_db.py adds user_type as
`TEXT NOT NULL DEFAULT 'new'`, and SQLite's ADD COLUMN with a constant
default fills every existing row with 'new' at ALTER time. So there are
normally zero NULL/empty rows for this script to touch.

It exists anyway as an auditable artifact and a guard against the edge
case where the column somehow holds a NULL or '' (e.g. a hand-run ALTER
that omitted the default, or a manual INSERT). Guarded + idempotent:

    UPDATE users SET user_type = 'new'
    WHERE user_type IS NULL OR TRIM(user_type) = ''

Run on the gateway VM as root, AFTER deploy.sh:

    sudo python3 /opt/miracle-router/migrations/v4_3_backfill_users.py --dry-run
    sudo python3 /opt/miracle-router/migrations/v4_3_backfill_users.py

Flags:
    --dry-run   Report how many rows would change; write nothing.
    --help      Show this message.
"""

import os
import sqlite3
import sys

DB_PATH = os.environ.get("MIRACLE_DB_PATH", "/etc/miracle-registry/miracle.db")

GUARD = "user_type IS NULL OR TRIM(user_type) = ''"

UPDATE_SQL = "UPDATE users SET user_type = 'new' WHERE " + GUARD


def die(msg, code=1):
    """Print `msg` to stderr and exit with status `code`."""
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def has_column(conn, table, col):
    """True if `table` currently has a column named `col`."""
    rows = conn.execute("PRAGMA table_info(%s)" % table).fetchall()
    return any(r[1] == col for r in rows)


def main():
    """CLI entry: safety-net backfill of users.user_type='new' on existing rows
    (usually a no-op -- ADD COLUMN already filled the default). --dry-run previews."""
    dry_run = "--dry-run" in sys.argv
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        die("ERROR: must run as root (sudo).")

    if not os.path.exists(DB_PATH):
        die("ERROR: DB not found at %s" % DB_PATH)

    print("=" * 60)
    print(" v4_3 backfill -- users.user_type (safety no-op)")
    print(" mode    : %s" % ("DRY RUN" if dry_run else "APPLY"))
    print(" DB path : %s" % DB_PATH)
    print("=" * 60)

    conn = sqlite3.connect(DB_PATH)
    try:
        if not has_column(conn, "users", "user_type"):
            die("ERROR: users.user_type missing. Run init_db.py first "
                "(sudo python3 /opt/miracle-router/init_db.py).", 2)

        total   = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        to_fill = conn.execute(
            "SELECT COUNT(*) FROM users WHERE " + GUARD).fetchone()[0]
        print("  users total            : %d" % total)
        print("  NULL/empty user_type   : %d" % to_fill)
        print("  already set            : %d" % (total - to_fill))
        print()

        if to_fill == 0:
            print("Nothing to do -- the ADD COLUMN default already filled "
                  "every row with 'new'.")
            print("=" * 60)
            return

        if dry_run:
            print("DRY RUN -- would set %d rows to user_type='new'." % to_fill)
            print("=" * 60)
            return

        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(UPDATE_SQL)
        conn.execute("COMMIT")
        print("  rows updated           : %d" % cur.rowcount)
        print("Backfill complete.")
        print("=" * 60)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
