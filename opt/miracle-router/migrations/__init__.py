"""One-shot data-backfill migrations (v4_2 … v4_5).

These are NOT the idempotent schema sync (that is init_db.py). Each script
here populates existing rows once, is guarded to be re-run-safe, and is run
manually after init_db.py on a deploy. See migrations/README.md for the roster
and run order.
"""
