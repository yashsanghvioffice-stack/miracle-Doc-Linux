#!/usr/bin/env python3
"""
v4.1 legacy-user backfill -- users.start_date AND users.user_type.

Pre-v4.1 user rows can have NULL start_date (and, on any DB where the
column was added without a default, NULL/blank user_type). In the grouped
User-wise Report those rows collapse into an ugly batch with a blank
User Type and blank Start Date. This one-shot script cleans them:

    start_date  = date(created_at)   WHERE start_date IS NULL
    user_type   = 'new'              WHERE user_type IS NULL OR user_type = ''

Two guarded SQL UPDATEs in one transaction -- idempotent (a 2nd run finds
nothing), atomic, and it never touches rows that are already set.

NOTE: init_db.py adds user_type as `NOT NULL DEFAULT 'new'`, so on a
normally-migrated DB the user_type half is already done at ALTER time and
this reports 0 there -- it's a belt-and-suspenders for odd/legacy DBs.
(This supersedes the old v4_3_backfill_users.py, which did user_type only.)

Run on the gateway VM as root, AFTER deploy.sh (which runs init_db.py):

    sudo python3 /opt/miracle-router/migrations/v4_4_backfill_user_start.py --dry-run
    sudo python3 /opt/miracle-router/migrations/v4_4_backfill_user_start.py

Flags:
    --dry-run   Report how many rows would change + a sample; write nothing.
    --help      Show this message.
"""

import os
import sqlite3
import sys

DB_PATH = os.environ.get("MIRACLE_DB_PATH", "/etc/miracle-registry/miracle.db")

START_GUARD = "start_date IS NULL"
TYPE_GUARD  = "user_type IS NULL OR TRIM(user_type) = ''"

UPDATE_START = "UPDATE users SET start_date = date(created_at) WHERE " + START_GUARD
UPDATE_TYPE  = "UPDATE users SET user_type  = 'new'            WHERE " + TYPE_GUARD

SAMPLE_SQL = """
    SELECT id, username,
           date(created_at) AS start_would_be,
           CASE WHEN user_type IS NULL OR TRIM(user_type) = '' THEN 'new'
                ELSE user_type END AS type_would_be
    FROM   users
    WHERE  {sg} OR ({tg})
    ORDER  BY id
    LIMIT  10
""".format(sg=START_GUARD, tg=TYPE_GUARD)


def die(msg, code=1):
    """Print `msg` to stderr and exit with status `code`."""
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def has_column(conn, table, col):
    """True if `table` currently has a column named `col`."""
    rows = conn.execute("PRAGMA table_info(%s)" % table).fetchall()
    return any(r[1] == col for r in rows)


def main():
    """CLI entry: fill-only backfill of users.start_date=date(created_at) and
    user_type='new' on existing rows. --dry-run previews. See module docstring."""
    dry_run = "--dry-run" in sys.argv
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        die("ERROR: must run as root (sudo).")

    if not os.path.exists(DB_PATH):
        die("ERROR: DB not found at %s" % DB_PATH)

    print("=" * 60)
    print(" v4_4 backfill -- users.start_date + users.user_type")
    print(" mode    : %s" % ("DRY RUN" if dry_run else "APPLY"))
    print(" DB path : %s" % DB_PATH)
    print("=" * 60)

    conn = sqlite3.connect(DB_PATH)
    try:
        for col in ("start_date", "user_type", "created_at"):
            if not has_column(conn, "users", col):
                die("ERROR: users.%s missing. Run init_db.py first "
                    "(sudo python3 /opt/miracle-router/init_db.py)." % col, 2)

        total       = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        need_start  = conn.execute(
            "SELECT COUNT(*) FROM users WHERE " + START_GUARD).fetchone()[0]
        need_type   = conn.execute(
            "SELECT COUNT(*) FROM users WHERE " + TYPE_GUARD).fetchone()[0]
        print("  users total              : %d" % total)
        print("  NULL start_date          : %d" % need_start)
        print("  NULL/blank user_type     : %d" % need_type)
        print()

        if need_start == 0 and need_type == 0:
            print("Nothing to do -- every user already has start_date + user_type.")
            print("=" * 60)
            return

        print("Sample of rows that %s be set:" % ("would" if dry_run else "will"))
        for r in conn.execute(SAMPLE_SQL).fetchall():
            print("   id=%-4s %-20s start->%s  type->%s" % (r[0], r[1], r[2], r[3]))
        print()

        if dry_run:
            print("DRY RUN -- no changes written.")
            print("=" * 60)
            return

        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        cs = conn.execute(UPDATE_START).rowcount
        ct = conn.execute(UPDATE_TYPE).rowcount
        conn.execute("COMMIT")

        rem_start = conn.execute(
            "SELECT COUNT(*) FROM users WHERE " + START_GUARD).fetchone()[0]
        rem_type = conn.execute(
            "SELECT COUNT(*) FROM users WHERE " + TYPE_GUARD).fetchone()[0]
        print("  start_date rows updated  : %d  (remaining NULL: %d)" % (cs, rem_start))
        print("  user_type  rows updated  : %d  (remaining NULL/blank: %d)" % (ct, rem_type))
        print()
        print("Backfill complete." if (rem_start == 0 and rem_type == 0)
              else "WARNING: some rows still unset -- investigate.")
        print("=" * 60)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
