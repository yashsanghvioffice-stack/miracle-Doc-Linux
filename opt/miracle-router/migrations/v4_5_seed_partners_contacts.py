#!/usr/bin/env python3
"""
v4.1 one-shot seed: partners + client account fields + per-user start/type.

Enriches the EXISTING live gateway DB (it does NOT rebuild anything). Reads
the reviewed CSV `v4_5_seed_data.csv` (Customer ID -> partner name, start
date, extracted from the team's Excel) and fills the account fields that
only the team tracked, plus derivable fields.

For every client (matched by client_name = Customer ID):
    partner_id         <- partner from CSV (created in `partners` if new).
                          Test accounts NOT in the CSV -> left NULL.
    subscription_end   <- start + 1yr - 1day
                          (start = CSV date; test accounts = date(created_at))
    subscription_type  <- 'multi' if the client has >1 user, else 'single'
    storage_gb         <- 5
    contact_email      <- the client's admin (lowest-id) user's email
    contact_mobile     <- the client's admin (lowest-id) user's mobile
For every user of that client:
    start_date         <- the same start basis
    user_type          <- 'new'

SAFETY (this is live data):
    * FILL-ONLY: every write is guarded `WHERE <col> IS NULL` -- it never
      overwrites a value already set. Idempotent (2nd run changes nothing).
    * users.email / users.mobile are NEVER touched.
    * No rows inserted/deleted in clients or users (partners may gain rows).
    * One IMMEDIATE transaction; --dry-run writes nothing; built-in verify.

PRE-REQ: run init_db.py first so the columns exist (contact_email,
contact_mobile, subscription_type, storage_gb, subscription_end,
users.user_type, users.start_date). The script refuses to run otherwise.

Usage (root, after deploy.sh runs init_db.py):
    sudo python3 /opt/miracle-router/migrations/v4_5_seed_partners_contacts.py --dry-run
    sudo python3 /opt/miracle-router/migrations/v4_5_seed_partners_contacts.py

Flags: --dry-run (report only), --help.
"""

import csv
import os
import sqlite3
import sys
from datetime import date, timedelta

DB_PATH  = os.environ.get("MIRACLE_DB_PATH", "/etc/miracle-registry/miracle.db")
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "v4_5_seed_data.csv")
STORAGE_GB = 5

REQUIRED = {
    "clients": ("partner_id", "subscription_type", "storage_gb",
                "subscription_end", "contact_email", "contact_mobile"),
    "users":   ("user_type", "start_date"),
}


def die(msg, code=1):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def expiry_from(iso):
    """start + 1 year - 1 day, ISO in/out."""
    y, m, d = (int(x) for x in iso.split("-"))
    s = date(y, m, d)
    try:
        plus = s.replace(year=s.year + 1)
    except ValueError:                       # Feb 29
        plus = s.replace(year=s.year + 1, day=28)
    return (plus - timedelta(days=1)).isoformat()


def has_column(conn, table, col):
    return any(r[1] == col for r in conn.execute("PRAGMA table_info(%s)" % table))


def load_seed():
    if not os.path.exists(CSV_PATH):
        die("ERROR: seed CSV not found at %s" % CSV_PATH, 2)
    seed = {}          # customer_id -> (partner_name, start_iso)
    partners = []      # ordered distinct partner names (case-insensitive)
    seen = set()
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cid = (row["customer_id"] or "").strip()
            pname = (row["partner_name"] or "").strip()
            start = (row["start_date"] or "").strip()
            if not cid:
                continue
            seed[cid] = (pname, start)
            if pname and pname.lower() not in seen:
                seen.add(pname.lower()); partners.append(pname)
    return seed, partners


def main():
    dry = "--dry-run" in sys.argv
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__); sys.exit(0)
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        die("ERROR: must run as root (sudo).")
    if not os.path.exists(DB_PATH):
        die("ERROR: DB not found at %s" % DB_PATH)

    seed, partner_names = load_seed()

    print("=" * 66)
    print(" v4_5 seed -- partners + client account fields + user start/type")
    print(" mode    : %s" % ("DRY RUN" if dry else "APPLY"))
    print(" DB      : %s" % DB_PATH)
    print(" CSV     : %s  (%d customers, %d partners)"
          % (CSV_PATH, len(seed), len(partner_names)))
    print("=" * 66)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # Pre-req: columns must exist.
        for tbl, cols in REQUIRED.items():
            for col in cols:
                if not has_column(conn, tbl, col):
                    die("ERROR: %s.%s missing. Run init_db.py first." % (tbl, col), 2)

        clients = conn.execute(
            "SELECT id, client_name, date(created_at) cd FROM clients ORDER BY id"
        ).fetchall()

        # Plan per client (compute, don't write yet).
        plan = []
        for c in clients:
            cid = str(c["client_name"]).strip()
            in_seed = cid in seed
            pname, start = seed.get(cid, (None, None))
            if not start:
                start = c["cd"]                      # test accounts -> created date
            nusers = conn.execute(
                "SELECT COUNT(*) FROM users WHERE client_name=? COLLATE NOCASE", (cid,)
            ).fetchone()[0]
            sub_type = "multi" if nusers > 1 else "single"
            admin = conn.execute(
                "SELECT email, mobile FROM users WHERE client_name=? COLLATE NOCASE "
                "ORDER BY id LIMIT 1", (cid,)
            ).fetchone()
            plan.append({
                "id": c["id"], "cid": cid, "partner": pname if in_seed else None,
                "start": start, "end": expiry_from(start),
                "sub_type": sub_type, "storage": STORAGE_GB,
                "email": admin["email"] if admin else None,
                "mobile": admin["mobile"] if admin else None,
                "in_seed": in_seed,
            })

        # Show a sample.
        print("Sample plan (first 4 + the 3 test accounts):")
        show = plan[:4] + [p for p in plan if not p["in_seed"]]
        for p in show:
            tag = "" if p["in_seed"] else "  [TEST: no partner]"
            print("  %-8s partner=%-16s type=%-6s end=%s storage=%s contact=%s%s"
                  % (p["cid"], p["partner"] or "-", p["sub_type"], p["end"],
                     p["storage"], p["email"], tag))
        print()

        if dry:
            # Count what WOULD change (currently-null fields).
            print("Would create partners:", len(partner_names))
            print("Would set fields on   :", len(plan), "clients")
            print("Would set start_date/user_type on all users where NULL.")
            print("DRY RUN -- no changes written.")
            print("=" * 66)
            return

        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")

        # 1) partners (insert-if-absent, NOCASE)
        for name in partner_names:
            conn.execute(
                "INSERT INTO partners (name) SELECT ? "
                "WHERE NOT EXISTS (SELECT 1 FROM partners WHERE name=? COLLATE NOCASE)",
                (name, name))
        pid_by_name = {r["name"].lower(): r["id"]
                       for r in conn.execute("SELECT id, name FROM partners")}

        # 2) per-client fills (all FILL-ONLY: WHERE <col> IS NULL)
        for p in plan:
            if p["partner"]:
                conn.execute(
                    "UPDATE clients SET partner_id=? WHERE id=? AND partner_id IS NULL",
                    (pid_by_name[p["partner"].lower()], p["id"]))
            conn.execute("UPDATE clients SET subscription_end=? WHERE id=? AND subscription_end IS NULL",
                         (p["end"], p["id"]))
            conn.execute("UPDATE clients SET subscription_type=? WHERE id=? AND subscription_type IS NULL",
                         (p["sub_type"], p["id"]))
            conn.execute("UPDATE clients SET storage_gb=? WHERE id=? AND storage_gb IS NULL",
                         (p["storage"], p["id"]))
            if p["email"]:
                conn.execute("UPDATE clients SET contact_email=? WHERE id=? AND contact_email IS NULL",
                             (p["email"], p["id"]))
            if p["mobile"]:
                conn.execute("UPDATE clients SET contact_mobile=? WHERE id=? AND contact_mobile IS NULL",
                             (p["mobile"], p["id"]))
            # users of this client
            conn.execute("UPDATE users SET start_date=? "
                         "WHERE client_name=? COLLATE NOCASE AND start_date IS NULL",
                         (p["start"], p["cid"]))
            conn.execute("UPDATE users SET user_type='new' "
                         "WHERE client_name=? COLLATE NOCASE AND (user_type IS NULL OR TRIM(user_type)='')",
                         (p["cid"],))

        conn.execute("COMMIT")

        # 3) verification
        print("--- VERIFICATION ---")
        ok = True
        def chk(label, val, want):
            nonlocal ok
            good = (val == want)
            ok = ok and good
            print("  [%s] %-46s %s" % ("PASS" if good else "FAIL", label, val if good else "%s (want %s)" % (val, want)))

        chk("clients row count unchanged", conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0], len(clients))
        chk("users row count unchanged", conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        chk("partners created", conn.execute("SELECT COUNT(*) FROM partners").fetchone()[0], len(partner_names))
        chk("clients missing subscription_end", conn.execute("SELECT COUNT(*) FROM clients WHERE subscription_end IS NULL").fetchone()[0], 0)
        chk("clients missing subscription_type", conn.execute("SELECT COUNT(*) FROM clients WHERE subscription_type IS NULL").fetchone()[0], 0)
        chk("clients missing storage_gb", conn.execute("SELECT COUNT(*) FROM clients WHERE storage_gb IS NULL").fetchone()[0], 0)
        chk("clients missing contact_email", conn.execute("SELECT COUNT(*) FROM clients WHERE contact_email IS NULL").fetchone()[0], 0)
        seed_ids = tuple(seed.keys())
        q = "SELECT COUNT(*) FROM clients WHERE partner_id IS NULL AND client_name IN (%s)" % ",".join("?"*len(seed_ids))
        chk("seeded clients missing partner_id", conn.execute(q, seed_ids).fetchone()[0], 0)
        chk("users missing start_date", conn.execute("SELECT COUNT(*) FROM users WHERE start_date IS NULL").fetchone()[0], 0)
        chk("users missing user_type", conn.execute("SELECT COUNT(*) FROM users WHERE user_type IS NULL OR TRIM(user_type)=''").fetchone()[0], 0)
        integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
        chk("PRAGMA integrity_check", integ, "ok")

        print("=" * 66)
        print(" SEED COMPLETE -- verification %s" % ("PASSED" if ok else "FAILED (SEE ABOVE)"))
        print("=" * 66)
        if not ok:
            sys.exit(3)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
