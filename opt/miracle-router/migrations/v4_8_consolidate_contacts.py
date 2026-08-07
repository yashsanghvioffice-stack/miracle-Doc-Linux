#!/usr/bin/env python3
"""
v4_8 one-shot: consolidate per-user contacts into the client (account) row.

    users.email   ->  clients.contact_email     (MERGE all distinct addresses)
    users.mobile  ->  clients.contact_mobile    (MERGE all distinct numbers)

MERGE RULE (chosen 2026-08-06): nothing is discarded. For each client the new
value is:

    existing clients.contact_* values, in order
    + every distinct users.* value for that client, in user-id order
    de-duped, lower-cased (email) / normalized (mobile), joined with ","

Both columns already accept multiple comma-separated values, so a client with
three users keeps all three addresses. An "admin user only" rule would have
silently dropped the rest -- that is exactly what this avoids.

users.email / users.mobile are **NOT** modified and **NOT** dropped. They stay
as dead columns (still NOT NULL DEFAULT '') so this migration is fully
reversible: if the merge is ever judged wrong, the source data is untouched.
Dropping them is a separate, later decision.

CAP: contact_email / contact_mobile are capped at 20 values (the BL limit --
storing more would make a later PUT unvalidatable). If a merge would exceed
the cap the extras are NOT silently dropped: the client is listed under
"OVER CAP" and skipped entirely, for you to resolve by hand.

ORPHANS: a user whose client_name has no clients row has nowhere to merge
into. Those are reported and left alone -- never silently dropped.

IDEMPOTENT: re-running finds every address already present and writes nothing.

SAFETY: one IMMEDIATE transaction; --dry-run writes nothing; built-in verify
asserts that every user contact is present in its client's merged value.

Usage (root):
    sudo python3 v4_8_consolidate_contacts.py --dry-run
    sudo python3 v4_8_consolidate_contacts.py
    sudo python3 v4_8_consolidate_contacts.py --verify

BACK UP FIRST:
    sudo sqlite3 /etc/miracle-registry/miracle.db ".backup /root/miracle.pre-v4_8.db"
"""

import os
import re
import sqlite3
import sys

DB_PATH   = os.environ.get("MIRACLE_DB_PATH", "/etc/miracle-registry/miracle.db")
MAX_VALS  = 20                                   # clients_bl CONTACT_*_MAX_COUNT
MOBILE_RE = re.compile(r"^\+?[0-9]{7,15}$")
EMAIL_RE  = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def split_vals(raw):
    """Split a stored comma-separated contact field into a clean list."""
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def merge(existing_raw, incoming, kind):
    """Merge `incoming` values into the existing stored list.

    Order-preserving and de-duped: existing values keep their position, new
    ones are appended. Emails are lower-cased; mobiles keep their form.
    Invalid values are skipped (reported by the caller via `skipped`).
    Returns (merged_list, skipped_list).
    """
    rx = EMAIL_RE if kind == "email" else MOBILE_RE
    out, seen, skipped = [], set(), []
    for v in split_vals(existing_raw):
        key = v.lower()
        if key not in seen:
            seen.add(key)
            out.append(v.lower() if kind == "email" else v)
    for v in incoming:
        v = (v or "").strip()
        if not v:
            continue
        if not rx.match(v):
            skipped.append(v)
            continue
        key = v.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(v.lower() if kind == "email" else v)
    return out, skipped


def main():
    """Merge per-user contacts up into the client row. See module docstring."""
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
    conn.row_factory = sqlite3.Row

    print("=" * 70)
    print(" v4_8 -- consolidate users.email/mobile -> clients.contact_*")
    print(" MERGE RULE: keep every distinct value (nothing discarded)")
    if verify:
        print(" MODE: VERIFY (read-only)")
    elif dry:
        print(" MODE: DRY RUN (no changes)")
    print("=" * 70)
    print("  DB: %s\n" % DB_PATH)

    ucols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    ccols = {r[1] for r in conn.execute("PRAGMA table_info(clients)")}
    for need, cols, tbl in (("email", ucols, "users"), ("mobile", ucols, "users"),
                            ("contact_email", ccols, "clients"),
                            ("contact_mobile", ccols, "clients")):
        if need not in cols:
            sys.stderr.write("ERROR: %s.%s missing -- run init_db.py first.\n" % (tbl, need))
            sys.exit(1)

    plan, over_cap, skipped_all = [], [], []
    for c in conn.execute("SELECT id, client_name, contact_email, contact_mobile "
                          "FROM clients ORDER BY client_name").fetchall():
        us = conn.execute(
            "SELECT email, mobile FROM users WHERE client_name = ? COLLATE NOCASE ORDER BY id",
            (c["client_name"],)).fetchall()

        new_e, sk_e = merge(c["contact_email"],  [u["email"]  for u in us], "email")
        new_m, sk_m = merge(c["contact_mobile"], [u["mobile"] for u in us], "mobile")
        for v in sk_e:
            skipped_all.append((c["client_name"], "email", v))
        for v in sk_m:
            skipped_all.append((c["client_name"], "mobile", v))

        if len(new_e) > MAX_VALS or len(new_m) > MAX_VALS:
            over_cap.append((c["client_name"], len(new_e), len(new_m)))
            continue

        cur_e = ",".join(split_vals(c["contact_email"]))
        cur_m = ",".join(split_vals(c["contact_mobile"]))
        val_e, val_m = ",".join(new_e), ",".join(new_m)
        if val_e != cur_e or val_m != cur_m:
            plan.append({"id": c["id"], "name": c["client_name"],
                         "old_e": cur_e, "new_e": val_e,
                         "old_m": cur_m, "new_m": val_m})

    orphans = conn.execute("""
        SELECT u.username, u.client_name, u.email, u.mobile FROM users u
        WHERE (TRIM(IFNULL(u.email,'')) <> '' OR TRIM(IFNULL(u.mobile,'')) <> '')
          AND NOT EXISTS (SELECT 1 FROM clients c
                          WHERE c.client_name = u.client_name COLLATE NOCASE)
        ORDER BY u.username
    """).fetchall()

    print("--- Plan ---")
    print("  clients needing an update : %d" % len(plan))
    for p in plan[:25]:
        if p["new_e"] != p["old_e"]:
            print("    %-14s email  %-34s -> %s"
                  % (p["name"], p["old_e"] or "(none)", p["new_e"] or "(none)"))
        if p["new_m"] != p["old_m"]:
            print("    %-14s mobile %-34s -> %s"
                  % (p["name"], p["old_m"] or "(none)", p["new_m"] or "(none)"))
    if len(plan) > 25:
        print("    ... %d more client(s)" % (len(plan) - 25))

    if over_cap:
        print("\n  !! OVER CAP (%d values max) -- SKIPPED, resolve by hand:" % MAX_VALS)
        for name, ne, nm in over_cap:
            print("       %-14s emails=%d mobiles=%d" % (name, ne, nm))
    if skipped_all:
        print("\n  !! Malformed values skipped (left in users.*, not merged):")
        for name, kind, v in skipped_all[:20]:
            print("       %-14s %-6s %r" % (name, kind, v))
        if len(skipped_all) > 20:
            print("       ... %d more" % (len(skipped_all) - 20))
    if orphans:
        print("\n  !! Users with NO clients row -- nothing to merge into:")
        for o in orphans[:20]:
            print("       %-18s client_name=%r" % (o["username"], o["client_name"]))
        if len(orphans) > 20:
            print("       ... %d more" % (len(orphans) - 20))

    if verify:
        bad = 0
        for c in conn.execute("SELECT client_name, contact_email, contact_mobile FROM clients").fetchall():
            have_e = {v.lower() for v in split_vals(c["contact_email"])}
            have_m = {v.lower() for v in split_vals(c["contact_mobile"])}
            for u in conn.execute("SELECT username, email, mobile FROM users "
                                  "WHERE client_name = ? COLLATE NOCASE",
                                  (c["client_name"],)).fetchall():
                e, m = (u["email"] or "").strip().lower(), (u["mobile"] or "").strip().lower()
                if e and EMAIL_RE.match(e) and e not in have_e:
                    print("  [FAIL] %s: %s missing from contact_email" % (c["client_name"], e)); bad += 1
                if m and MOBILE_RE.match(m) and m not in have_m:
                    print("  [FAIL] %s: %s missing from contact_mobile" % (c["client_name"], m)); bad += 1
        print("\n" + "=" * 70)
        if bad:
            print(" VERIFY FAILED: %d user contact(s) not represented on their client." % bad)
            sys.exit(2)
        print(" VERIFY OK -- every user contact is present on its client row.")
        print("=" * 70)
        sys.exit(0)

    if dry:
        print("\n" + "=" * 70)
        print(" DRY RUN -- nothing written. %d client(s) would change." % len(plan))
        print("=" * 70)
        sys.exit(0)

    if not plan:
        print("\nNothing to do -- already consolidated.")
        sys.exit(0)

    conn.execute("BEGIN IMMEDIATE")
    for p in plan:
        conn.execute("UPDATE clients SET contact_email = ?, contact_mobile = ? WHERE id = ?",
                     (p["new_e"] or None, p["new_m"] or None, p["id"]))
    conn.execute("COMMIT")

    integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
    print("\n--- Applied ---")
    print("  clients updated  : %d" % len(plan))
    print("  integrity_check  : %s" % integ)
    print("  users.email/mobile left UNTOUCHED (dead columns, reversible)")
    print("\n" + "=" * 70)
    print(" v4_8 complete. Now verify:")
    print("   sudo python3 %s --verify" % os.path.basename(__file__))
    print("=" * 70)
    conn.close()


if __name__ == "__main__":
    main()
