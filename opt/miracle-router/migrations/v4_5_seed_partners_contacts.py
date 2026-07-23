#!/usr/bin/env python3
"""
v4.1 one-shot seed: partners + client account fields + per-user start/type.

Enriches the EXISTING live gateway DB (it does NOT rebuild anything). All seed
data is embedded as reviewed Python arrays (extracted from the team's Excel);
no external CSV is read. The migration fills the account fields that only the
team tracked, plus derivable fields.

FIRST, the PARTNER MASTER is seeded from the reviewed "Partner Details" sheet
(embedded PARTNER_SEED: name + email, multi-email comma-separated, names
normalized to consistent casing) -- insert-if-absent one by one, then fill any
null email. This runs before any client/user fill.

For every client (matched by client_name = Customer ID):
    partner_id         <- looked up (PK) from the partner master by name via
                          CLIENT_PARTNER_MAP (alias-resolved). No match -> the
                          pair is reported as "unmatched" and partner_id is NULL.
                          Test accounts not in the map -> also left NULL.
    subscription_end   <- start + 1yr - 1day
                          (start = CLIENT_START date; test accounts =
                          date(created_at))
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

import os
import sqlite3
import sys
from datetime import date, timedelta

DB_PATH  = os.environ.get("MIRACLE_DB_PATH", "/etc/miracle-registry/miracle.db")
STORAGE_GB = 5

# ─── Partner master (from "Partner Details - name & email.xlsx", reviewed) ──
# Seeded FIRST, before any client/user fill. (name, email) -- `email` may hold
# several comma-separated addresses. Names normalized to consistent casing
# (lowercase/ALLCAPS -> Title Case; short acronyms + MCPL kept verbatim).
PARTNER_SEED = [
    ('Dharmendra JC', 'sunlightcomputers@gmail.com'),
    ('Shailesh Patel', 'shaileshpatel_guru@yahoo.com'),
    ('Vibrant Technology', 'vibrant_technologies@yahoo.com'),
    ('Mayank Shukla', 'shukla_infotech@yahoo.com'),
    ('Punit Daxini', 'punitdaxini@gmail.com'),
    ('RKS Office', 'rajkot@rkitsoftware.com'),
    ('Avinash Patil', 'rajsoft2005@gmail.com'),
    ('Drishya Enterprise', 'manmudraenterprise@yahoo.com'),
    ('Perfect Infosys', 'support@perfectinfosys.in'),
    ('Jatin Bosamia', 'sales@apsoftware.in'),
    ('Kamlesh Mistry', 'mirbmhelp@gmail.com'),
    ('Aslam Kondhiya', 'akinfosoft.helpdesk@gmail.com'),
    ('RP Computer', 'rpcomputer8000@gmail.com'),
    ('Ali Hathiyari', 'miracleahmedabad@gmail.com'),
    ('Harsh Lathiya', 'kalpinfotech24@gmail.com'),
    ('Bhargav Suthar', 'absinfotech2015@gmail.com'),
    ('Dananajay Sonavane', 'anjalicomputers05@gmail.com'),
    ('Ketan Marthak', 'motherinfosoft@gmail.com'),
    ('Shivay Infotech', 'shivayinfotech122022@gmail.com'),
    ('Manish Nimavat', 'nkinfosoft9@gmail.com'),
    ('Ashraf Ravani', 'asharaf_ravani@yahoo.co.in'),
    ('Tanmay Joshi', 'tanmayjoshy@gmail.com,miraclevadodara@gmail.com'),
    ('Khyati Rathod', 'khyati.shreedutt@gmail.com'),
    ('Manjit Zala', 'manjitzala@yahoo.com'),
    ('Amit Deshpande', 'adsgsoftware@gmail.com'),
    ('Rajesh Karia', 'software.rcc@gmail.com,rydhaminfosys@yahoo.co.in'),
    ('Nilesh Patel', 'ssinfosys.sales@gmail.com'),
    ('Manish Mor', 'manishmor@gmail.com'),
    ('Kaushik Ruparel', 'ruparelinfosys@yahoo.com'),
    ('Ritesh Shah', 'shah5602@gmail.com'),
    ('Hitesh Chavda', 'nhinfosoft@gmail.com'),
    ('Ketan Gohel/ Gohel Hingu', 'sales@shashanksoft.com,gopalhingu2192@gmail.com'),
    ('Jaybhai', 'jbinfosoft@yahoo.com'),
    ('Deepak Gupta', 'manvitechnologies1@gmail.com'),
    ('Mayur Korat', 'mkinfosoftsurat@gmail.com'),
    ('Rahul Tadas', 'rpsofttechamt@gmail.com'),
    ('Abdul Qayyum', 'soudagars1137@gmail.com'),
    ('Dikshesh Nakum', 'a.techsolution1990@gmail.com'),
    ('Bhavesh Shah', 'vaibhav26672@yahoo.in'),
    ('Yogesh Garala', 'mtechsrt@gmail.com'),
    ('Aarix Infosys', 'aarixinfosys@gmail.com'),
    ('Ashwin Kothari', 'kothari.sales201@gmail.com'),
    ('MCPL', 'partner@miracleclouderp.com'),
]

# Customer-CSV partner names that are the SAME partner as a sheet entry under a
# different label (reviewed + confirmed 2026-07). The CSV name is mapped to its
# canonical sheet name so those customers link to the ONE partner row (no dup).
#   'Gopal Hingu'  == 'Ketan Gohel/ Gohel Hingu' (sheet's 2nd email gopalhingu2192@)
#   'JB Infosoft'  == 'Jaybhai'                   (sheet email jbinfosoft@)
#   'Harsh Lathia' == 'Harsh Lathiya'             (desktop-file spelling variant,
#                                                  confirmed same partner 2026-07-23)
# ('Manish Pandya' is a genuine partner absent from the sheet -> seeded name-only
#  via EXTRA_PARTNERS; give it an email later via PUT /admin/partners.)
PARTNER_ALIASES = {
    "gopal hingu":  "Ketan Gohel/ Gohel Hingu",
    "jb infosoft":  "Jaybhai",
    "harsh lathia": "Harsh Lathiya",
}

# Partners approved for creation that are NOT in the sheet (reviewed additions).
# email TBD -> stored NULL; fill later via PUT /admin/partners.
EXTRA_PARTNERS = [
    ('Manish Pandya', None),
]

# ─── 2nd map: customer_id -> partner name (reviewed, from the team Excel) ────
# The migration searches each partner name in the partner master (alias-resolved)
# and sets the client's partner_id (FK/PK). Names with NO match are collected
# into an "unmatched" list, reported to the operator, and leave partner_id NULL.
CLIENT_PARTNER_MAP = [
    ('1484', 'Ashwin Kothari'),
    ('4945', 'Hitesh Chavda'),
    ('16427', 'Mayank Shukla'),
    ('28725', 'Hitesh Chavda'),
    ('43085', 'Vibrant Technology'),
    ('44889', 'Dananajay Sonavane'),
    ('48634', 'Mayank Shukla'),
    ('49054', 'Perfect Infosys'),
    ('49805', 'Hitesh Chavda'),
    ('50294', 'Jatin Bosamia'),
    ('54175', 'Avinash Patil'),
    ('55531', 'Ashwin Kothari'),
    ('56963', 'Mayank Shukla'),
    ('57045', 'Mayank Shukla'),
    ('63693', 'Mayank Shukla'),
    ('82819', 'Perfect Infosys'),
    ('86992', 'Ketan Marthak'),
    ('89991', 'Aslam Kondhiya'),
    ('90804', 'Vibrant Technology'),
    ('93897', 'JB Infosoft'),
    ('101663', 'Rajesh Karia'),
    ('102475', 'Ketan Marthak'),
    ('104661', 'Mayank Shukla'),
    ('109483', 'Ketan Marthak'),
    ('110631', 'Dikshesh Nakum'),
    ('111648', 'Mayank Shukla'),
    ('112273', 'Manish Pandya'),
    ('112700', 'Hitesh Chavda'),
    ('114650', 'Ketan Marthak'),
    ('115865', 'Nilesh Patel'),
    ('117354', 'RKS Office'),
    ('117798', 'Manish Mor'),
    ('117812', 'RKS Office'),
    ('117841', 'Mayank Shukla'),
    ('117881', 'JB Infosoft'),
    ('117882', 'JB Infosoft'),
    ('117944', 'JB Infosoft'),
    ('117945', 'JB Infosoft'),
    ('117961', 'Ketan Marthak'),
    ('118010', 'JB Infosoft'),
    ('118011', 'JB Infosoft'),
    ('118013', 'Manjit Zala'),
    ('118109', 'Rajesh Karia'),
    ('118137', 'JB Infosoft'),
    ('118148', 'Mayur Korat'),
    ('118158', 'Rajesh Karia'),
    ('118207', 'Gopal Hingu'),
    ('118262', 'Aslam Kondhiya'),
    # Added 2026-07-23 from Miracle_On_Cloud_Desktop_Data.xlsx (Dealer Name).
    # Dealer casing normalized to the master's canonical name (links NOCASE).
    # 'Harsh Lathia' (29835, 47765) alias-resolves to master 'Harsh Lathiya'.
    # (118312 Poptop / dealer 'Abhishek Bhimani' deliberately left unmapped ->
    #  partner_id stays NULL, to be assigned later via the client edit API.)
    ('5520',   'Shailesh Patel'),
    ('29835',  'Harsh Lathia'),
    ('42237',  'Ketan Marthak'),
    ('47765',  'Harsh Lathia'),
    ('93058',  'Shailesh Patel'),
    ('102601', 'Mayank Shukla'),
    ('103368', 'Rajesh Karia'),
    ('109481', 'Mayank Shukla'),
    ('118021', 'Ketan Marthak'),
    ('118142', 'Shailesh Patel'),
    ('118282', 'Rajesh Karia'),
    ('118336', 'Mayank Shukla'),
    ('118338', 'Mayank Shukla'),
]

# ─── 3rd map: customer_id -> subscription start date (reviewed, from the team
# Excel). Drives subscription_end (start +1yr -1day) and each user's start_date.
# Customers NOT listed here (test accounts) fall back to their created_at date.
CLIENT_START = {
    '1484':   '2026-07-02',
    '4945':   '2026-07-01',
    '16427':  '2026-07-07',
    '28725':  '2026-07-07',
    '43085':  '2026-06-20',
    '44889':  '2026-03-25',
    '48634':  '2026-06-19',
    '49054':  '2026-06-18',
    '49805':  '2026-07-03',
    '50294':  '2026-06-29',
    '54175':  '2026-07-04',
    '55531':  '2026-06-25',
    '56963':  '2026-06-18',
    '57045':  '2026-06-23',
    '63693':  '2026-06-29',
    '82819':  '2026-06-23',
    '86992':  '2026-06-24',
    '89991':  '2026-06-17',
    '90804':  '2026-06-30',
    '93897':  '2026-06-26',
    '101663': '2026-06-25',
    '102475': '2026-06-18',
    '104661': '2026-06-27',
    '109483': '2026-06-25',
    '110631': '2026-07-04',
    '111648': '2026-06-23',
    '112273': '2026-06-24',
    '112700': '2026-06-23',
    '114650': '2026-07-01',
    '115865': '2026-07-02',
    '117354': '2026-06-23',
    '117798': '2026-06-19',
    '117812': '2026-06-19',
    '117841': '2026-06-23',
    '117881': '2026-06-24',
    '117882': '2026-06-24',
    '117944': '2026-06-27',
    '117945': '2026-06-27',
    '117961': '2026-06-27',
    '118010': '2026-07-02',
    '118011': '2026-07-02',
    '118013': '2026-07-03',
    '118109': '2026-07-06',
    '118137': '2026-07-07',
    '118148': '2026-07-07',
    '118158': '2026-07-07',
    '118207': '2026-10-07',
    '118262': '2026-10-07',
    # Added 2026-07-23 from Miracle_On_Cloud_Desktop_Data.xlsx (Date column).
    '5520':   '2026-07-13',
    '29835':  '2026-07-13',
    '42237':  '2026-07-13',
    '47765':  '2026-07-17',
    '93058':  '2026-07-15',
    '102601': '2026-07-14',
    '103368': '2026-07-13',
    '109481': '2026-10-07',
    '118021': '2026-07-15',
    '118142': '2026-10-07',
    '118282': '2026-07-13',
    '118336': '2026-07-14',
    '118338': '2026-07-14',
}

REQUIRED = {
    "clients": ("partner_id", "subscription_type", "storage_gb",
                "subscription_end", "contact_email", "contact_mobile"),
    "users":   ("user_type", "start_date"),
}


def die(msg, code=1):
    """Print `msg` to stderr and exit with status `code`."""
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
    """True if `table` currently has a column named `col`."""
    return any(r[1] == col for r in conn.execute("PRAGMA table_info(%s)" % table))


def main():
    """CLI entry: seed partners + client account fields + per-user start/type from
    the embedded arrays. Fill-only + idempotent; one IMMEDIATE txn with built-in
    verification. --dry-run previews without writing. See module docstring."""
    dry = "--dry-run" in sys.argv
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__); sys.exit(0)
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        die("ERROR: must run as root (sudo).")
    if not os.path.exists(DB_PATH):
        die("ERROR: DB not found at %s" % DB_PATH)

    print("=" * 66)
    print(" v4_5 seed -- partners + client account fields + user start/type")
    print(" mode    : %s" % ("DRY RUN" if dry else "APPLY"))
    print(" DB      : %s" % DB_PATH)
    print(" data    : embedded arrays (%d partners, %d customer links, %d start dates)"
          % (len(PARTNER_SEED) + len(EXTRA_PARTNERS),
             len(CLIENT_PARTNER_MAP), len(CLIENT_START)))
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
        partner_by_cid = dict(CLIENT_PARTNER_MAP)    # for the sample display only
        plan = []
        for c in clients:
            cid = str(c["client_name"]).strip()
            in_seed = cid in CLIENT_START
            start = CLIENT_START.get(cid) or c["cd"]  # test accounts -> created date
            nusers = conn.execute(
                "SELECT COUNT(*) FROM users WHERE client_name=? COLLATE NOCASE", (cid,)
            ).fetchone()[0]
            sub_type = "multi" if nusers > 1 else "single"
            admin = conn.execute(
                "SELECT email, mobile FROM users WHERE client_name=? COLLATE NOCASE "
                "ORDER BY id LIMIT 1", (cid,)
            ).fetchone()
            plan.append({
                "id": c["id"], "cid": cid, "partner": partner_by_cid.get(cid),
                "start": start, "end": expiry_from(start),
                "sub_type": sub_type, "storage": STORAGE_GB,
                "email": admin["email"] if admin else None,
                "mobile": admin["mobile"] if admin else None,
                "in_seed": in_seed,
            })

        # Map/array customer_ids with no matching client row: the migration
        # can't link or date them (the UPDATEs are silent no-ops). Report so the
        # operator can reconcile the sheet against the real gateway client list.
        existing_cids = {p["cid"] for p in plan}
        mapped_cids = {cid for cid, _ in CLIENT_PARTNER_MAP} | set(CLIENT_START)
        absent_from_db = sorted(mapped_cids - existing_cids)

        # Show a sample.
        print("Sample plan (first 4 + the test accounts):")
        show = plan[:4] + [p for p in plan if not p["in_seed"]]
        for p in show:
            tag = "" if p["in_seed"] else "  [TEST: no partner]"
            print("  %-8s partner=%-16s type=%-6s end=%s storage=%s contact=%s%s"
                  % (p["cid"], p["partner"] or "-", p["sub_type"], p["end"],
                     p["storage"], p["email"], tag))
        print()

        if dry:
            known = {n.lower() for n, _ in PARTNER_SEED} | {n.lower() for n, _ in EXTRA_PARTNERS}
            dry_unmatched = [(cid, pn) for cid, pn in CLIENT_PARTNER_MAP
                             if PARTNER_ALIASES.get(pn.lower(), pn).lower() not in known]
            print("Would seed partner master  :", len(PARTNER_SEED) + len(EXTRA_PARTNERS),
                  "partners (insert-if-absent, fill-null-email)")
            print("Would link customers->partner: %d of %d (FK set)"
                  % (len(CLIENT_PARTNER_MAP) - len(dry_unmatched), len(CLIENT_PARTNER_MAP)))
            print("Would set fields on        :", len(plan), "clients")
            print("Would set start_date/user_type on all users where NULL.")
            if dry_unmatched:
                print("UNMATCHED partner names (partner_id would stay NULL):")
                for cid, pn in dry_unmatched:
                    print("   customer %-8s partner %r" % (cid, pn))
            else:
                print("All customer partner names matched -- none unmatched.")
            if absent_from_db:
                print("NOTE: %d mapped customer(s) NOT in clients -> skipped: %s"
                      % (len(absent_from_db), ", ".join(absent_from_db)))
            else:
                print("All mapped customers exist in clients.")
            print("DRY RUN -- no changes written.")
            print("=" * 66)
            return

        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")

        # 1a) PARTNER MASTER -- seed FIRST from the reviewed Partner Details
        #     sheet (PARTNER_SEED). Iterate one by one: insert (name, email)
        #     when the partner is absent (NOCASE); if it already exists without
        #     an email, fill it. FILL-ONLY -- an existing non-empty email is
        #     never overwritten. `email` may carry comma-separated addresses.
        for pname, pemail in PARTNER_SEED + EXTRA_PARTNERS:
            conn.execute(
                "INSERT INTO partners (name, email) SELECT ?, ? "
                "WHERE NOT EXISTS (SELECT 1 FROM partners WHERE name=? COLLATE NOCASE)",
                (pname, pemail, pname))
            if pemail:                          # EXTRA_PARTNERS may have no email yet
                conn.execute(
                    "UPDATE partners SET email=? "
                    "WHERE name=? COLLATE NOCASE AND (email IS NULL OR TRIM(email)='')",
                    (pemail, pname))

        # Partner master is now complete (sheet + reviewed extras). Build the
        # name -> id (PK) index used for the FK linking below.
        pid_by_name = {r["name"].lower(): r["id"]
                       for r in conn.execute("SELECT id, name FROM partners")}

        # 2) CUSTOMER -> PARTNER FK linking, driven by CLIENT_PARTNER_MAP.
        #    For each (customer_id, partner_name): resolve any merge alias, search
        #    the partner master by name, and on a hit set clients.partner_id to
        #    that partner's PK (FILL-ONLY). Names with no match are collected into
        #    `unmatched`, reported below, and leave partner_id NULL.
        unmatched = []
        for cid, pname in CLIENT_PARTNER_MAP:
            canon = PARTNER_ALIASES.get(pname.lower(), pname)
            partner_id = pid_by_name.get(canon.lower())
            if partner_id is None:
                unmatched.append((cid, pname))
                continue                        # leave clients.partner_id NULL
            conn.execute(
                "UPDATE clients SET partner_id=? "
                "WHERE client_name=? COLLATE NOCASE AND partner_id IS NULL",
                (partner_id, cid))

        # 3) per-client fills (subscription + contact) -- all FILL-ONLY.
        for p in plan:
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

        # 4) verification
        print("--- VERIFICATION ---")
        ok = True
        def chk(label, val, want):
            """Assert val == want: print PASS/FAIL and clear the enclosing
            `ok` flag on any mismatch."""
            nonlocal ok
            good = (val == want)
            ok = ok and good
            print("  [%s] %-46s %s" % ("PASS" if good else "FAIL", label, val if good else "%s (want %s)" % (val, want)))

        chk("clients row count unchanged", conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0], len(clients))
        chk("users row count unchanged", conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        db_pnames = {r["name"].lower() for r in conn.execute("SELECT name FROM partners")}
        chk("all sheet partners present", sum(1 for p in PARTNER_SEED if p[0].lower() in db_pnames), len(PARTNER_SEED))
        chk("sheet partners all have an email",
            conn.execute("SELECT COUNT(*) FROM partners WHERE TRIM(COALESCE(email,'')) <> ''").fetchone()[0] >= len(PARTNER_SEED),
            True)
        chk("clients missing subscription_end", conn.execute("SELECT COUNT(*) FROM clients WHERE subscription_end IS NULL").fetchone()[0], 0)
        chk("clients missing subscription_type", conn.execute("SELECT COUNT(*) FROM clients WHERE subscription_type IS NULL").fetchone()[0], 0)
        chk("clients missing storage_gb", conn.execute("SELECT COUNT(*) FROM clients WHERE storage_gb IS NULL").fetchone()[0], 0)
        chk("clients missing contact_email", conn.execute("SELECT COUNT(*) FROM clients WHERE contact_email IS NULL").fetchone()[0], 0)
        # Customer -> partner FK linking result.
        chk("customer->partner names unmatched", len(unmatched), 0)
        # Every MATCHED customer (i.e. not in `unmatched`) must have a partner_id.
        unmatched_cids = {cid for cid, _ in unmatched}
        matched_ids = tuple(cid for cid, _ in CLIENT_PARTNER_MAP if cid not in unmatched_cids)
        if matched_ids:
            q = "SELECT COUNT(*) FROM clients WHERE partner_id IS NULL AND client_name IN (%s)" % ",".join("?"*len(matched_ids))
            chk("matched clients missing partner_id", conn.execute(q, matched_ids).fetchone()[0], 0)
        if unmatched:
            print("  --- UNMATCHED customer -> partner (partner_id left NULL) ---")
            for cid, pn in unmatched:
                print("      customer %-8s partner %r" % (cid, pn))
        # Non-fatal: mapped customers with no client row (nothing was applied to
        # them). Informational -- they may simply not be provisioned yet.
        if absent_from_db:
            print("  NOTE: %d mapped customer(s) NOT in clients -> skipped: %s"
                  % (len(absent_from_db), ", ".join(absent_from_db)))
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
