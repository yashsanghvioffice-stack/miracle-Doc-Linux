#!/usr/bin/env python3
"""
Miracle Cloud Gateway -- migrate_v6.py

ADDITIVE migration. Creates the `clients` table for the uKey concept.
Does NOT touch users, server_master, or rdp_download_tokens.

Idempotent: safe to run multiple times.

Run on the gateway VM as root:
    sudo systemctl stop miracle-router
    sudo python3 /opt/miracle-router/migrate_v6.py
    sudo systemctl start miracle-router

Schema added:
    clients (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        client_name  TEXT NOT NULL UNIQUE COLLATE NOCASE,
        ukey         TEXT NOT NULL UNIQUE COLLATE NOCASE,
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        CHECK(length(ukey) = 8)
    )
    INDEX idx_clients_ukey ON clients(ukey COLLATE NOCASE)
"""

import os
import sqlite3
import sys

DB_PATH = os.environ.get("MIRACLE_DB_PATH", "/etc/miracle-registry/miracle.db")


def main():
    if os.geteuid() != 0:
        sys.stderr.write("ERROR: must run as root (sudo).\n")
        sys.exit(1)

    if not os.path.exists(DB_PATH):
        sys.stderr.write(
            "ERROR: Database not found at {}\n"
            "       Run migrate_v3.py and migrate_v5.py first.\n".format(DB_PATH)
        )
        sys.exit(1)

    print("migrate_v6: adding clients table to {}".format(DB_PATH))

    conn = sqlite3.connect(DB_PATH)
    try:
        # Confirm prerequisites
        existing = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for req in ("server_master", "users", "rdp_download_tokens"):
            if req not in existing:
                sys.stderr.write(
                    "ERROR: required table '{}' missing.\n"
                    "       Run migrate_v3.py and migrate_v5.py first.\n".format(req)
                )
                sys.exit(1)

        if "clients" in existing:
            print("  clients table already exists -- nothing to do.")
        else:
            conn.executescript("""
                CREATE TABLE clients (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_name  TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    ukey         TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CHECK(length(ukey) = 8)
                );
                CREATE INDEX idx_clients_ukey ON clients(ukey COLLATE NOCASE);
            """)
            conn.commit()
            print("  Created clients table + idx_clients_ukey")

        # Final verification
        cnt = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        print("  clients row count: {}".format(cnt))

    finally:
        conn.close()

    print("migrate_v6: done.")
    print()
    print("Next:")
    print("  sudo systemctl start miracle-router")
    print("  curl -s http://127.0.0.1:5001/health | jq")


if __name__ == "__main__":
    main()