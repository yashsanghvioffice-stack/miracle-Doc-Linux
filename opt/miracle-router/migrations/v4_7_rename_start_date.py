#!/usr/bin/env python3
"""
v4_7 one-shot: rename users.start_date -> users.subscription_start.

Per-user subscription dates become the authoritative grain (v4.3+): the start
date lives on the user, not the client. The column already held exactly that
value -- this is a pure RENAME, no value is computed, derived or defaulted.
Every row keeps the date it already had.

    users.start_date  ->  users.subscription_start          (DATE, nullable)

WHY A MIGRATION AND NOT init_db.py:
init_db.py only CREATEs tables and ADDs columns -- by design it never renames.
If it ran first against an un-migrated DB it would ADD an EMPTY
`subscription_start` alongside the populated `start_date`, and every start date
would read back NULL. init_db.py now REFUSES to run in that situation and
points here. See the DUPLICATE state below for the recovery path.

STATE DETECTION (this is what makes it idempotent -- no user_version needed):
    start_date only          -> RENAME. The normal path.
    subscription_start only  -> already migrated. No-op, exit 0.
    BOTH present             -> DUPLICATE: init_db.py added an empty column
                                before this ran. Backfills subscription_start
                                from start_date wherever it is NULL, then
                                leaves start_date in place as a dead column
                                (never dropped -- DROP COLUMN needs SQLite
                                3.35+ and dropping live data is not something
                                this script will do unattended).
    neither present          -> refuses; the table is not what we expect.

SAFETY:
    * requires SQLite >= 3.25 (ALTER TABLE ... RENAME COLUMN)
    * one IMMEDIATE transaction -- all-or-nothing
    * non-NULL count is captured BEFORE and re-checked AFTER; any drift aborts
    * PRAGMA integrity_check at the end
    * --dry-run writes nothing; --verify is read-only

Usage (root, BEFORE init_db.py on an un-migrated DB):
    sudo python3 /opt/miracle-router/migrations/v4_7_rename_start_date.py --dry-run
    sudo python3 /opt/miracle-router/migrations/v4_7_rename_start_date.py
    sudo python3 /opt/miracle-router/migrations/v4_7_rename_start_date.py --verify

BACK UP FIRST. This edits a live registry:
    sudo cp /etc/miracle-registry/miracle.db /root/miracle.db.pre-v4_7
"""

import os
import sqlite3
import sys

DB_PATH = os.environ.get("MIRACLE_DB_PATH", "/etc/miracle-registry/miracle.db")

OLD_COL = "start_date"
NEW_COL = "subscription_start"
TABLE   = "users"

# ALTER TABLE ... RENAME COLUMN landed in SQLite 3.25.0.
MIN_SQLITE = (3, 25, 0)


def columns(conn, table):
    """Set of column names on `table` (empty set if the table is absent)."""
    return {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table).fetchall()}


def sqlite_version_tuple(conn):
    """Runtime SQLite version as an int tuple, e.g. (3, 50, 4)."""
    v = conn.execute("SELECT sqlite_version()").fetchone()[0]
    return tuple(int(p) for p in v.split(".")), v


def non_null(conn, col):
    """How many users rows carry a non-NULL value in `col`."""
    return conn.execute(
        "SELECT COUNT(*) FROM users WHERE %s IS NOT NULL" % col).fetchone()[0]


def describe(conn, col):
    """(non-null count, min, max) for a date column -- the fingerprint we use
    to prove the rename moved the data rather than replacing it."""
    row = conn.execute(
        "SELECT COUNT(%s), MIN(%s), MAX(%s) FROM users" % (col, col, col)
    ).fetchone()
    return row[0], row[1], row[2]


def main():
    """Rename users.start_date -> users.subscription_start.

    Idempotent via state detection (see module docstring). --dry-run previews,
    --verify is a read-only check that exits non-zero if the DB is not in the
    post-migration state."""
    dry    = "--dry-run" in sys.argv
    verify = "--verify" in sys.argv

    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    if not (dry or verify) and hasattr(os, "geteuid") and os.geteuid() != 0:
        sys.stderr.write("ERROR: must run as root (sudo).\n")
        sys.exit(1)

    if not os.path.exists(DB_PATH):
        sys.stderr.write("ERROR: DB not found at %s\n" % DB_PATH)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    try:
        ver_tuple, ver_str = sqlite_version_tuple(conn)
        cols = columns(conn, TABLE)

        print("=" * 64)
        print(" v4_7 -- rename users.%s -> users.%s" % (OLD_COL, NEW_COL))
        if verify:
            print(" MODE: VERIFY (read-only)")
        elif dry:
            print(" MODE: DRY RUN (no changes)")
        print("=" * 64)
        print("  DB             : %s" % DB_PATH)
        print("  SQLite         : %s" % ver_str)
        print("  users rows     : %d" % conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        print("  has %-14s: %s" % (OLD_COL, OLD_COL in cols))
        print("  has %-14s: %s" % (NEW_COL, NEW_COL in cols))
        print()

        has_old, has_new = OLD_COL in cols, NEW_COL in cols

        # ── State: neither column -> not the schema we expect ──
        if not has_old and not has_new:
            sys.stderr.write(
                "ERROR: `users` has neither %s nor %s. This is not the expected\n"
                "       schema -- refusing to touch it. Run init_db.py first.\n"
                % (OLD_COL, NEW_COL))
            sys.exit(1)

        # ── State: already migrated ──
        if has_new and not has_old:
            n, lo, hi = describe(conn, NEW_COL)
            print("Already migrated -- `%s` is present and `%s` is gone."
                  % (NEW_COL, OLD_COL))
            print("  %s non-null: %d   range: %s .. %s" % (NEW_COL, n, lo, hi))
            print("Nothing to do.")
            sys.exit(0)

        if verify:
            sys.stderr.write(
                "VERIFY FAILED: `%s` still present / `%s` %s.\n"
                "               The rename has NOT been applied.\n"
                % (OLD_COL, NEW_COL, "missing" if not has_new else "also present"))
            sys.exit(2)

        # ── State: DUPLICATE -- init_db added an empty column before us ──
        if has_old and has_new:
            stale = conn.execute(
                "SELECT COUNT(*) FROM users WHERE %s IS NULL AND %s IS NOT NULL"
                % (NEW_COL, OLD_COL)).fetchone()[0]
            print("DUPLICATE STATE: both columns exist.")
            print("  init_db.py added an empty `%s` before this migration ran." % NEW_COL)
            print("  rows needing backfill from `%s`: %d" % (OLD_COL, stale))
            if dry:
                print("\nDRY RUN -- would backfill %d row(s), then leave `%s` as a"
                      " dead column (never dropped)." % (stale, OLD_COL))
                sys.exit(0)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE users SET %s = %s WHERE %s IS NULL AND %s IS NOT NULL"
                         % (NEW_COL, OLD_COL, NEW_COL, OLD_COL))
            conn.execute("COMMIT")
            n, lo, hi = describe(conn, NEW_COL)
            print("\nBackfilled. %s non-null: %d   range: %s .. %s" % (NEW_COL, n, lo, hi))
            print("NOTE: `%s` is left in place as a DEAD column (no longer read"
                  " or written).\n      Drop it manually later if you want:"
                  "  ALTER TABLE users DROP COLUMN %s;  (needs SQLite >= 3.35)"
                  % (OLD_COL, OLD_COL))
            print("=" * 64)
            sys.exit(0)

        # ── State: the normal path -- rename ──
        if ver_tuple < MIN_SQLITE:
            sys.stderr.write(
                "ERROR: SQLite %s is too old for ALTER TABLE RENAME COLUMN "
                "(need >= %s).\n" % (ver_str, ".".join(str(x) for x in MIN_SQLITE)))
            sys.exit(1)

        before_n, before_lo, before_hi = describe(conn, OLD_COL)
        print("BEFORE:  %s non-null: %d   range: %s .. %s"
              % (OLD_COL, before_n, before_lo, before_hi))

        if dry:
            print("\nWOULD RUN:  ALTER TABLE %s RENAME COLUMN %s TO %s"
                  % (TABLE, OLD_COL, NEW_COL))
            print("DRY RUN -- nothing written. %d row(s) would keep their dates."
                  % before_n)
            print("=" * 64)
            sys.exit(0)

        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ALTER TABLE %s RENAME COLUMN %s TO %s"
                     % (TABLE, OLD_COL, NEW_COL))
        conn.execute("COMMIT")

        # ── Verification: the data must be identical, only the name changed ──
        after_cols = columns(conn, TABLE)
        after_n, after_lo, after_hi = describe(conn, NEW_COL)
        print("AFTER :  %s non-null: %d   range: %s .. %s"
              % (NEW_COL, after_n, after_lo, after_hi))

        ok = True
        def chk(label, got, want):
            """Assert got == want; print PASS/FAIL and clear `ok` on mismatch."""
            nonlocal ok
            good = got == want
            ok = ok and good
            print("  [%s] %-34s %r%s" % ("PASS" if good else "FAIL", label, got,
                                         "" if good else " (expected %r)" % want))

        chk("old column gone",        OLD_COL in after_cols, False)
        chk("new column present",     NEW_COL in after_cols, True)
        chk("non-null count preserved", after_n,  before_n)
        chk("min date preserved",     after_lo, before_lo)
        chk("max date preserved",     after_hi, before_hi)
        chk("integrity_check",
            conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

        print("=" * 64)
        if not ok:
            sys.stderr.write("MIGRATION VERIFICATION FAILED -- restore your backup.\n")
            sys.exit(2)
        print(" v4_7 complete -- %d row(s) kept their dates." % after_n)
        print(" Next: sudo python3 /opt/miracle-router/init_db.py")
        print("=" * 64)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
