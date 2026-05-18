#!/usr/bin/env python3
"""
Miracle Cloud Gateway - Migration v3.3 -> v3.4

WHAT IT DOES
    Adds the rdp_download_tokens table used by the new .rdp download flow.
    Drops the now-obsolete remoteapp_sessions table (was used by the
    discarded remoteapps:// protocol-handler approach).

    Does NOT touch users or server_master tables.

    rdp_download_tokens schema:
        token       TEXT PRIMARY KEY   -- 32-char hex (secrets.token_hex(16))
        username    TEXT NOT NULL      -- for audit
        server_ip   TEXT NOT NULL      -- which TSplus the .rdp points at
        created_at  TEXT NOT NULL      -- token issued
        used_at     TEXT NULL          -- NULL until first download, then set

    Tokens are single-use and expire 5 minutes after creation. Cleanup is
    lazy: each new INSERT also DELETEs rows older than the TTL.

WHEN TO RUN
    Run once on the gateway VM after deploying router.py v3.4 files but
    before starting the service. Earlier migrations (v3, v4) must already
    have been run -- this script only adds and drops tables, it does not
    bootstrap the base schema.

USAGE
    sudo systemctl stop miracle-router
    sudo python3 migrate_v5.py
    sudo cp router.py /opt/miracle-router/router.py
    sudo systemctl start miracle-router
"""

import os
import shutil
import sqlite3
import sys
from datetime import datetime

DB_PATH = "/etc/miracle-registry/miracle.db"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS rdp_download_tokens (
    token       TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    server_ip   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    used_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_rdp_tokens_created
    ON rdp_download_tokens(created_at);
"""

DROP_SQL = "DROP TABLE IF EXISTS remoteapp_sessions"


def main():
    if not os.path.exists(DB_PATH):
        msg = (
            "FATAL: Database file not found at {}\n"
            "       This script only adds/drops tables on an existing DB.\n"
            "       Run migrate_v3.py first to create the base schema."
        ).format(DB_PATH)
        sys.stderr.write(msg + "\n")
        return 1

    # 1. Backup
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = "{}.bak.{}".format(DB_PATH, ts)
    shutil.copy2(DB_PATH, backup)
    print("[OK] Backup created: {}".format(backup))

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()

        # 2. Verify base schema is present
        existing = {
            row[0] for row in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {"server_master", "users"}
        missing = required - existing
        if missing:
            sys.stderr.write(
                "FATAL: Base tables missing: {}\n".format(", ".join(missing)) +
                "       Run migrate_v3.py first.\n"
            )
            return 2

        # 3. Report what is about to happen
        if "remoteapp_sessions" in existing:
            count = cur.execute(
                "SELECT COUNT(*) FROM remoteapp_sessions"
            ).fetchone()[0]
            print("[INFO] Will DROP obsolete table 'remoteapp_sessions' ({} rows).".format(count))

        if "rdp_download_tokens" in existing:
            count = cur.execute(
                "SELECT COUNT(*) FROM rdp_download_tokens"
            ).fetchone()[0]
            print("[INFO] Table 'rdp_download_tokens' already exists ({} rows). Skipping create.".format(count))

        # 4. Create new table
        cur.executescript(CREATE_SQL)
        print("[OK] Created table: rdp_download_tokens (+ index)")

        # 5. Drop obsolete table
        cur.execute(DROP_SQL)
        print("[OK] Dropped obsolete table: remoteapp_sessions (if existed)")

        conn.commit()

        # 6. Verify
        tables_after = {
            row[0] for row in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "rdp_download_tokens" not in tables_after:
            sys.stderr.write("FATAL: Table 'rdp_download_tokens' missing after migration\n")
            return 3
        if "remoteapp_sessions" in tables_after:
            sys.stderr.write("WARN: Table 'remoteapp_sessions' still present after migration\n")
        print("[OK] Verification passed.")

        # 7. Final schema summary
        print("")
        print("Tables present after migration:")
        for t in sorted(tables_after):
            print("  - " + t)

    finally:
        conn.close()

    # 8. Fix ownership
    try:
        import pwd
        uid = pwd.getpwnam("miracle").pw_uid
        gid = pwd.getpwnam("miracle").pw_gid
        os.chown(DB_PATH, uid, gid)
        os.chmod(DB_PATH, 0o644)
        print("[OK] DB ownership: miracle:miracle, mode 644")
    except (KeyError, PermissionError) as e:
        print("[WARN] Could not set ownership ({}).".format(e))
        print("       If gunicorn runs as a non-root user, run:")
        print("       sudo chown miracle:miracle {}".format(DB_PATH))

    print("")
    print("=" * 60)
    print(" Migration v3.3 -> v3.4 complete.")
    print("=" * 60)
    print(" Next steps:")
    print("   1. Deploy the new router.py to /opt/miracle-router/router.py")
    print("   2. Deploy the new workspace-remote.html and index.html")
    print("      to /var/www/miracle/")
    print("   3. Deploy the new miracle.cloud nginx config")
    print("   4. sudo nginx -t && sudo systemctl reload nginx")
    print("   5. sudo systemctl start miracle-router")
    print("   6. Test: login with RemoteApp preference, .rdp file downloads")
    print("")
    print(" Backup retained at: {}".format(backup))
    print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
