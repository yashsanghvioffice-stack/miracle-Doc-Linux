"""
DAL: database connection + schema verification.

Two responsibilities:

1. db()            - context manager yielding a `sqlite3.Connection`
                     with row_factory set, FK enforcement enabled, and
                     auto-commit/rollback wrapping. Used by every DAL
                     function that touches the DB.

2. verify_schema() - startup sanity check. Confirms the DB file exists
                     and all REQUIRED_TABLES are present. Logs + exits
                     non-zero on any failure. App does NOT create or
                     alter tables -- DDL ownership is external
                     (see /opt/miracle-router/init_db.py).

This module is the only one in DAL that imports `logger` and `messages`,
because verify_schema() emits operator-facing FATAL messages at startup.
Pure CRUD DAL modules should remain silent (errors propagate up).
"""

import os
import sqlite3
import sys
from contextlib import contextmanager

import messages as M
from config import DB_PATH, REQUIRED_TABLES
from logger import log


@contextmanager
def db():
    """Yield a SQLite connection with sensible defaults.

    - row_factory = sqlite3.Row     (column-name access on rows)
    - PRAGMA foreign_keys = ON      (we have FKs; enforce them)
    - auto-commit on clean exit
    - auto-rollback on any exception
    - always close
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def verify_schema():
    """Confirm required tables exist. Called once at app startup.

    Exits non-zero (and logs the FATAL message) if:
      - the DB file is missing
      - the DB cannot be opened (corruption, perms)
      - any of REQUIRED_TABLES is absent
    """
    if not os.path.exists(DB_PATH):
        msg = M.MSG_DB_NOT_FOUND_TMPL.format(DB_PATH)
        log.error(msg)
        sys.stderr.write(msg + "\n")
        sys.exit(1)

    try:
        with db() as conn:
            existing = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
    except sqlite3.Error as e:
        msg = M.MSG_DB_CANNOT_OPEN_TMPL.format(DB_PATH, e)
        log.error(msg)
        sys.stderr.write(msg + "\n")
        sys.exit(1)

    missing = [t for t in REQUIRED_TABLES if t not in existing]
    if missing:
        msg = M.MSG_DB_MISSING_TABLES_TMPL.format(DB_PATH, ", ".join(missing))
        log.error(msg)
        sys.stderr.write(msg + "\n")
        sys.exit(1)

    log.info("Schema check OK. Tables present: %s", sorted(existing))
