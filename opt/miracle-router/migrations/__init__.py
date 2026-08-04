"""One-shot data-backfill migrations (v4_5 seed, v4_6 timezone).

These are NOT the idempotent schema sync (that is init_db.py). Each script
here populates existing rows once, is guarded to be re-run-safe, and is run
manually after init_db.py on a deploy. See migrations/README.md for the roster
and run order.

(v4_2/v4_3/v4_4 were removed 2026-07-28 -- fully superseded by v4_5; still in
git history if ever needed.)
"""
