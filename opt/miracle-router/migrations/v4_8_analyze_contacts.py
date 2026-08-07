#!/usr/bin/env python3
"""
v4_8 ANALYSIS (READ-ONLY): what would happen if users.email / users.mobile
were consolidated into clients.contact_email / clients.contact_mobile.

Writes NOTHING. Opens the DB read-only (mode=ro) so it is safe to point at a
live registry while the service is running.

Answers the questions that decide the merge rule:
    * How many clients have 0 / 1 / many DISTINCT user emails?
    * Which clients would LOSE an address under "admin user only"?
    * Where does clients.contact_email already disagree with its users?
    * How many users carry an email/mobile at all (the column is NOT NULL,
      so '' is the on-disk "unset" sentinel)?
    * Would any merged contact_email exceed the 20-address cap?

Usage:
    python3 v4_8_analyze_contacts.py                    # default DB path
    MIRACLE_DB_PATH=/path/to/copy.db python3 v4_8_analyze_contacts.py
    python3 v4_8_analyze_contacts.py --list             # per-client detail
"""

import os
import sqlite3
import sys

DB_PATH = os.environ.get("MIRACLE_DB_PATH", "/etc/miracle-registry/miracle.db")
MAX_ADDRS = 20          # clients_bl.CONTACT_EMAIL_MAX_COUNT


def norm_emails(raw):
    """Split a stored contact_email into a normalized ordered de-duped list."""
    out = []
    for part in (raw or "").split(","):
        v = part.strip().lower()
        if v and v not in out:
            out.append(v)
    return out


def main():
    """Report the consolidation impact. Read-only; exits 0 always."""
    detail = "--list" in sys.argv
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    if not os.path.exists(DB_PATH):
        sys.stderr.write("ERROR: DB not found at %s\n" % DB_PATH)
        sys.exit(1)

    # Read-only URI: cannot modify the live DB even by accident.
    conn = sqlite3.connect("file:%s?mode=ro" % DB_PATH.replace("?", "%3f"), uri=True)
    conn.row_factory = sqlite3.Row

    print("=" * 70)
    print(" v4_8 ANALYSIS -- users.email/mobile -> clients.contact_*  (READ-ONLY)")
    print("=" * 70)
    print("  DB: %s\n" % DB_PATH)

    ucols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    ccols = {r[1] for r in conn.execute("PRAGMA table_info(clients)")}
    for need, cols, tbl in (("email", ucols, "users"), ("mobile", ucols, "users"),
                            ("contact_email", ccols, "clients"),
                            ("contact_mobile", ccols, "clients")):
        if need not in cols:
            sys.stderr.write("ERROR: %s.%s missing -- unexpected schema.\n" % (tbl, need))
            sys.exit(1)

    n_clients = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
    n_users   = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    u_email   = conn.execute("SELECT COUNT(*) FROM users WHERE TRIM(IFNULL(email,''))  <> ''").fetchone()[0]
    u_mobile  = conn.execute("SELECT COUNT(*) FROM users WHERE TRIM(IFNULL(mobile,'')) <> ''").fetchone()[0]
    c_email   = conn.execute("SELECT COUNT(*) FROM clients WHERE TRIM(IFNULL(contact_email,''))  <> ''").fetchone()[0]
    c_mobile  = conn.execute("SELECT COUNT(*) FROM clients WHERE TRIM(IFNULL(contact_mobile,'')) <> ''").fetchone()[0]

    print("--- Current state ---")
    print("  clients                     : %d" % n_clients)
    print("  users                       : %d" % n_users)
    print("  users WITH an email         : %d  (%d blank)" % (u_email, n_users - u_email))
    print("  users WITH a mobile         : %d  (%d blank)" % (u_mobile, n_users - u_mobile))
    print("  clients WITH contact_email  : %d  (%d blank)" % (c_email, n_clients - c_email))
    print("  clients WITH contact_mobile : %d  (%d blank)" % (c_mobile, n_clients - c_mobile))

    rows = conn.execute("""
        SELECT c.id, c.client_name, c.contact_email, c.contact_mobile
        FROM clients c ORDER BY c.client_name
    """).fetchall()

    buckets = {0: 0, 1: 0, "many": 0}
    would_lose, already_ok, conflicts, over_cap, orphan_users = [], [], [], [], 0

    for c in rows:
        us = conn.execute("""
            SELECT id, email, mobile FROM users
            WHERE client_name = ? COLLATE NOCASE ORDER BY id
        """, (c["client_name"],)).fetchall()

        emails = []
        for u in us:
            e = (u["email"] or "").strip().lower()
            if e and e not in emails:
                emails.append(e)

        if len(emails) == 0:
            buckets[0] += 1
        elif len(emails) == 1:
            buckets[1] += 1
        else:
            buckets["many"] += 1
            # Under rule (A) only the admin user's address survives.
            admin_e = ((us[0]["email"] or "").strip().lower()) if us else ""
            would_lose.append((c["client_name"], admin_e, [e for e in emails if e != admin_e]))

        existing = norm_emails(c["contact_email"])
        merged = existing + [e for e in emails if e not in existing]
        if len(merged) > MAX_ADDRS:
            over_cap.append((c["client_name"], len(merged)))
        if existing and emails:
            missing = [e for e in emails if e not in existing]
            if missing:
                conflicts.append((c["client_name"], c["contact_email"], missing))
            else:
                already_ok.append(c["client_name"])

    # Users whose client_name matches no clients row -- their contact has
    # nowhere to go, so consolidation would drop it entirely.
    orphan_users = conn.execute("""
        SELECT COUNT(*) FROM users u
        WHERE TRIM(IFNULL(u.email,'')) <> ''
          AND NOT EXISTS (SELECT 1 FROM clients c
                          WHERE c.client_name = u.client_name COLLATE NOCASE)
    """).fetchone()[0]

    print("\n--- Distinct user emails per client ---")
    print("  clients with 0 distinct emails : %d" % buckets[0])
    print("  clients with 1 distinct email  : %d   <- lossless either way" % buckets[1])
    print("  clients with MANY (>1)         : %d   <- THE decision" % buckets["many"])

    print("\n--- Impact of rule (A) 'admin user only' ---")
    if not would_lose:
        print("  No client would lose an address. (A) and (B) are equivalent here.")
    else:
        total_lost = sum(len(x[2]) for x in would_lose)
        print("  %d client(s) would DISCARD %d address(es)." % (len(would_lose), total_lost))
        for name, keep, lost in would_lose[:15 if not detail else len(would_lose)]:
            print("     %-14s keep %-30s lose %s" % (name, keep or "(none)", ", ".join(lost)))
        if not detail and len(would_lose) > 15:
            print("     ... %d more (run with --list)" % (len(would_lose) - 15))

    print("\n--- clients.contact_email already set but missing a user address ---")
    if not conflicts:
        print("  None -- every populated contact_email already covers its users.")
    else:
        print("  %d client(s):" % len(conflicts))
        for name, cur, missing in conflicts[:15 if not detail else len(conflicts)]:
            print("     %-14s has %-34s missing %s" % (name, cur, ", ".join(missing)))
        if not detail and len(conflicts) > 15:
            print("     ... %d more (run with --list)" % (len(conflicts) - 15))

    print("\n--- Cap / orphan checks ---")
    print("  merged contact_email over %d addresses : %s"
          % (MAX_ADDRS, ("%d client(s): %s" % (len(over_cap), over_cap)) if over_cap else "none"))
    print("  users with an email but NO clients row : %d%s"
          % (orphan_users, "   <- contact would be dropped" if orphan_users else ""))

    print("\n" + "=" * 70)
    print(" Nothing was written. Choose the merge rule, then run the migration.")
    print("=" * 70)
    conn.close()


if __name__ == "__main__":
    main()
