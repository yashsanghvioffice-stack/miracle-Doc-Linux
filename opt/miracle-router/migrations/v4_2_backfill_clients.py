#!/usr/bin/env python3
"""
Phase 2 data backfill -- clients.subscription_start / subscription_end.

init_db.py adds these two columns as nullable (SQLite ADD COLUMN cannot
take a date(created_at) default expression), so existing rows come out
NULL. This one-shot script populates them:

    subscription_start = date(created_at)
    subscription_end   = date(created_at, '+1 year', '-1 day')

The whole thing is a single guarded SQL UPDATE -- SQLite's date()
modifiers do the year math, so there's no row-by-row Python loop. It is:

    * idempotent  -- WHERE subscription_start IS NULL; a 2nd run finds
                     nothing and exits 0.
    * atomic      -- one UPDATE inside one transaction.
    * safe        -- never touches rows an operator already set by hand.

Run on the gateway VM as root, AFTER deploy.sh (which runs init_db.py):

    sudo python3 /opt/miracle-router/migrations/v4_2_backfill_clients.py --dry-run
    sudo python3 /opt/miracle-router/migrations/v4_2_backfill_clients.py

Flags:
    --dry-run   Report how many rows would change + a sample; write nothing.
    --help      Show this message.

Note on Feb-29: SQLite's date('...02-29','+1 year') normalises the
invalid 2025-02-29 to 2025-03-01, so end becomes 2025-02-28. This
differs by a day from the app-layer helper (bl/clients_bl.py) but never
arises for real created_at-derived dates in bulk.
"""

import os
import sqlite3
import sys

DB_PATH = os.environ.get("MIRACLE_DB_PATH", "/etc/miracle-registry/miracle.db")

GUARD = "subscription_start IS NULL"

UPDATE_SQL = """
    UPDATE clients
    SET subscription_start = date(created_at),
        subscription_end   = date(created_at, '+1 year', '-1 day')
    WHERE {guard}
""".format(guard=GUARD)

SAMPLE_SQL = """
    SELECT id, client_name,
           date(created_at)                          AS start_would_be,
           date(created_at, '+1 year', '-1 day')     AS end_would_be
    FROM   clients
    WHERE  {guard}
    ORDER  BY id
    LIMIT  10
""".format(guard=GUARD)


def die(msg, code=1):
    """Print `msg` to stderr and exit with status `code`."""
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def has_column(conn, table, col):
    """True if `table` currently has a column named `col`."""
    rows = conn.execute("PRAGMA table_info(%s)" % table).fetchall()
    return any(r[1] == col for r in rows)


def main():
    """CLI entry: fill-only backfill of clients.subscription_start/end on existing
    rows (start=date(created_at), end=+1yr-1day). --dry-run previews. See module docstring."""
    dry_run = "--dry-run" in sys.argv
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        die("ERROR: must run as root (sudo).")

    if not os.path.exists(DB_PATH):
        die("ERROR: DB not found at %s" % DB_PATH)

    print("=" * 60)
    print(" v4_2 backfill -- clients.subscription_start / _end")
    print(" mode    : %s" % ("DRY RUN" if dry_run else "APPLY"))
    print(" DB path : %s" % DB_PATH)
    print("=" * 60)

    conn = sqlite3.connect(DB_PATH)
    try:
        # Preconditions: columns must exist (init_db.py must have run first).
        for col in ("subscription_start", "subscription_end", "created_at"):
            if not has_column(conn, "clients", col):
                die("ERROR: clients.%s missing. Run init_db.py first "
                    "(sudo python3 /opt/miracle-router/init_db.py)." % col, 2)

        total    = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        to_fill  = conn.execute(
            "SELECT COUNT(*) FROM clients WHERE " + GUARD).fetchone()[0]
        print("  clients total          : %d" % total)
        print("  needing backfill (NULL): %d" % to_fill)
        print("  already set (skipped)  : %d" % (total - to_fill))
        print()

        if to_fill == 0:
            print("Nothing to do -- every client already has a subscription_start.")
            print("=" * 60)
            return

        # Show a sample either way.
        print("Sample of rows that %s be set:" %
              ("would" if dry_run else "will"))
        for r in conn.execute(SAMPLE_SQL).fetchall():
            print("   id=%-4s %-20s %s -> %s"
                  % (r[0], r[1], r[2], r[3]))
        print()

        if dry_run:
            print("DRY RUN -- no changes written.")
            print("=" * 60)
            return

        # Apply atomically.
        conn.isolation_level = None            # manual txn control
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(UPDATE_SQL)
        conn.execute("COMMIT")
        changed = cur.rowcount

        remaining = conn.execute(
            "SELECT COUNT(*) FROM clients WHERE " + GUARD).fetchone()[0]
        print("  rows updated           : %d" % changed)
        print("  remaining NULL         : %d" % remaining)
        print()
        print("Backfill complete." if remaining == 0
              else "WARNING: %d rows still NULL -- investigate." % remaining)
        print("=" * 60)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
