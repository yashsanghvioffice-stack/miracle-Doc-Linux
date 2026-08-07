"""
End-to-end self-test for the gateway.

Spins up the Flask app in-process against a throw-away SQLite DB and
exercises every endpoint that doesn't depend on a live TSplus server.
Run from the repo root on Windows:

    py -3 tmp/self_test.py

Exits 0 on success, non-zero on first failure.
"""

import os
import sys
import json
import tempfile

# 1. Build a sandboxed environment BEFORE importing the app
TMP_DB     = os.path.join(tempfile.gettempdir(), "miracle-selftest.db")
TMP_LOG    = os.path.join(tempfile.gettempdir(), "miracle-selftest.log")
TEST_KEY   = "self-test-api-key-9999"

if os.path.exists(TMP_DB):
    os.remove(TMP_DB)
for ext in ("-wal", "-shm"):
    if os.path.exists(TMP_DB + ext):
        os.remove(TMP_DB + ext)
if os.path.exists(TMP_LOG):
    os.remove(TMP_LOG)

os.environ["MIRACLE_DB_PATH"] = TMP_DB
os.environ["MIRACLE_API_KEY"] = TEST_KEY

# Make the router package importable
ROUTER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "opt", "miracle-router"))
sys.path.insert(0, ROUTER_DIR)

# 2. Bootstrap schema via init_db.py
# init_db requires geteuid==0 (Linux root); fake it on Windows.
import os as _os
if not hasattr(_os, "geteuid"):
    _os.geteuid = lambda: 0

import init_db
init_db.DB_PATH = TMP_DB
init_db.DB_DIR  = os.path.dirname(TMP_DB)
sys.argv = ["init_db.py"]
try:
    init_db.main()
except SystemExit as e:
    if e.code:
        raise SystemExit("init_db.py failed with exit %s" % e.code)

# 3. Patch LOG_PATH BEFORE the app imports logger
import config
config.LOG_PATH = TMP_LOG
# v4.1c: partner_id is mandatory on client create by default. The bulk of
# this suite predates that rule and creates clients without a partner, so
# relax it here; the dedicated Phase 4.1c block toggles it back ON to test
# enforcement, then OFF again.
config.REQUIRE_PARTNER = False

# 4. Import the Flask app
from router import app
client = app.test_client()


# ─── Test framework ─────────────────────────────────────────────
PASS = 0
FAIL = 0
H = {"X-API-Key": TEST_KEY}


def hit(method, path, **kwargs):
    fn = getattr(client, method.lower())
    return fn(path, **kwargs)


def check(name, resp, expect_status, expect_code=None, expect_keys=None):
    global PASS, FAIL
    body = {}
    try:
        body = json.loads(resp.data.decode() or "{}")
    except Exception:
        body = {"_raw": resp.data.decode(errors="replace")}
    ok = (resp.status_code == expect_status)
    if expect_code is not None and ok:
        ok = (body.get("code") == expect_code)
    if expect_keys and ok:
        ok = all(k in body for k in expect_keys)
    flag = "PASS" if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    snippet = json.dumps(body, default=str)[:160]
    print(" [%s] %-55s -> %d  %s" % (flag, name, resp.status_code, snippet))
    return body


# ─── 1. Health ──────────────────────────────────────────────────
print("\n=== Health & auth ===")
check("GET /health",
      hit("GET", "/health"),
      200)

check("GET /admin/servers without API key",
      hit("GET", "/admin/servers"),
      401, "INVALID_API_KEY")

check("GET /admin/servers wrong API key",
      hit("GET", "/admin/servers", headers={"X-API-Key": "bogus"}),
      401, "INVALID_API_KEY")

# ─── 2. Servers CRUD ────────────────────────────────────────────
print("\n=== Servers ===")
check("GET /admin/servers (empty)",
      hit("GET", "/admin/servers", headers=H),
      200)

body = check("POST /admin/servers create",
             hit("POST", "/admin/servers",
                 headers=H,
                 json={"server_name": "TestSvr", "server_ip": "10.0.0.99"}),
             201)
server_id = body.get("id")

check("POST /admin/servers duplicate name",
      hit("POST", "/admin/servers",
          headers=H,
          json={"server_name": "TestSvr", "server_ip": "10.0.0.100"}),
      409, "SERVER_NAME_EXISTS")

check("POST /admin/servers duplicate IP",
      hit("POST", "/admin/servers",
          headers=H,
          json={"server_name": "OtherSvr", "server_ip": "10.0.0.99"}),
      409, "SERVER_IP_EXISTS")

check("POST /admin/servers bad IP (validation)",
      hit("POST", "/admin/servers",
          headers=H,
          json={"server_name": "BadIP", "server_ip": "not.an.ip"}),
      400, "VALIDATION_FAILED")

check("GET /admin/servers/<id>",
      hit("GET", "/admin/servers/%s" % server_id, headers=H),
      200)

check("GET /admin/servers/99999 (not found)",
      hit("GET", "/admin/servers/99999", headers=H),
      404, "SERVER_NOT_FOUND")

# ─── 3. Clients CRUD ────────────────────────────────────────────
print("\n=== Clients ===")
body = check("POST /admin/clients create",
             hit("POST", "/admin/clients",
                 headers=H,
                 json={"client_name": "Acme", "ukey": "ACME1234"}),
             201)
client_id = body.get("id")

check("POST /admin/clients duplicate name",
      hit("POST", "/admin/clients",
          headers=H,
          json={"client_name": "Acme", "ukey": "OTHER123"}),
      409, "CLIENT_NAME_EXISTS")

check("POST /admin/clients duplicate ukey",
      hit("POST", "/admin/clients",
          headers=H,
          json={"client_name": "OtherCo", "ukey": "ACME1234"}),
      409, "UKEY_IN_USE")

check("POST /admin/clients bad ukey length",
      hit("POST", "/admin/clients",
          headers=H,
          json={"client_name": "BadKey", "ukey": "TOO_SHORT"}),
      400, "VALIDATION_FAILED")

body = check("GET /admin/clients (enriched fields)",
             hit("GET", "/admin/clients", headers=H),
             200)

check("GET /admin/clients/by-name/Acme",
      hit("GET", "/admin/clients/by-name/Acme", headers=H),
      200)

check("GET /admin/clients/exists/Acme",
      hit("GET", "/admin/clients/exists/Acme", headers=H),
      200)

# ─── 4. Users CRUD ──────────────────────────────────────────────
print("\n=== Users ===")
body = check("POST /admin/users create",
             hit("POST", "/admin/users",
                 headers=H,
                 json={
                     "username": "acme_user1",
                     "client_name": "Acme",
                     "server_id": server_id,
                     "password": "ignored-by-gateway",
                 }),
             201)
user_id = body.get("id")

check("POST /admin/users duplicate username",
      hit("POST", "/admin/users",
          headers=H,
          json={
              "username": "acme_user1",
              "client_name": "Acme",
              "server_id": server_id,
          }),
      409, "USERNAME_EXISTS")

check("POST /admin/users unknown client",
      hit("POST", "/admin/users",
          headers=H,
          json={
              "username": "ghost_user",
              "client_name": "NoSuchClient",
              "server_id": server_id,
          }),
      400, "UNKNOWN_CLIENT")

check("POST /admin/users unknown server",
      hit("POST", "/admin/users",
          headers=H,
          json={
              "username": "ghost_user2",
              "client_name": "Acme",
              "server_id": 99999,
          }),
      400, "UNKNOWN_SERVER")

check("POST /admin/users rejects email (account-level now)",
      hit("POST", "/admin/users",
          headers=H,
          json={
              "username": "badmail",
              "client_name": "Acme",
              "server_id": server_id,
              "email": "not-an-email",
          }),
      400, "VALIDATION_FAILED")

body = check("GET /admin/users?expand=true (includes ukey)",
             hit("GET", "/admin/users?expand=true", headers=H),
             200)
# Verify the response actually contains ukey for our user
users = body.get("users") or []
has_ukey = any(u.get("username") == "acme_user1" and u.get("ukey") == "ACME1234" for u in users)
print("       ukey on user record present: %s" % has_ukey)
if not has_ukey:
    FAIL += 1
    print(" [FAIL] /admin/users response missing ukey on acme_user1")
else:
    PASS += 1

check("POST /admin/users/<id>/disable",
      hit("POST", "/admin/users/%s/disable" % user_id, headers=H),
      200)

check("POST /admin/users/<id>/enable",
      hit("POST", "/admin/users/%s/enable" % user_id, headers=H),
      200)

# Cannot delete server that has users
check("DELETE /admin/servers/<id> blocked by FK",
      hit("DELETE", "/admin/servers/%s" % server_id, headers=H),
      409, "CANNOT_DELETE_SERVER_WITH_USERS")

# ─── 5. Stats ───────────────────────────────────────────────────
print("\n=== Stats ===")
check("GET /admin/stats",
      hit("GET", "/admin/stats", headers=H),
      200)

# ─── 6. /login (bind-miss path; no TSplus required) ─────────────
print("\n=== Login ===")
check("POST /login missing fields",
      hit("POST", "/login", json={}),
      400, "MISSING_FIELDS")

check("POST /login missing ukey",
      hit("POST", "/login", json={"username": "x", "password": "y"}),
      400, "MISSING_UKEY")

check("POST /login bad ukey format",
      hit("POST", "/login", json={"username": "x", "password": "y", "ukey": "BAD"}),
      400, "INVALID_UKEY")

check("POST /login bind-miss (no such user)",
      hit("POST", "/login",
          json={"username": "nobody", "password": "x", "ukey": "ACME1234"}),
      401, "INVALID_CREDENTIALS")

# ─── 7. /rdp/download ───────────────────────────────────────────
print("\n=== /rdp/download ===")
r = hit("GET", "/rdp/download/bogus")
ok = r.status_code in (400, 404)
print(" [%s] /rdp/download bogus token             -> %d" % ("PASS" if ok else "FAIL", r.status_code))
if ok: PASS += 1
else:  FAIL += 1

# ─── 7b. Explicit display_name on create / update ──────────────
print("\n=== Client display_name ===")
body = check("POST /admin/clients with explicit display_name",
             hit("POST", "/admin/clients",
                 headers=H,
                 json={"client_name": "1056",
                       "display_name": "RKIT Software",
                       "ukey": "RKIT5678"}),
             201)
rkit_id = body.get("id")
if body.get("display_name") == "RKIT Software" and body.get("client_name") == "1056":
    PASS += 1
    print(" [PASS] display_name persisted distinct from client_name")
else:
    FAIL += 1
    print(" [FAIL] expected display_name='RKIT Software' got: %s" % body)

# Verify display_name appears on the user record via JOIN (USER_SELECT)
hit("POST", "/admin/users", headers=H,
    json={"username": "rkit_u1", "client_name": "1056",
          "server_id": server_id})
body = check("GET /admin/users?expand=true carries display_name via JOIN",
             hit("GET", "/admin/users?expand=true", headers=H),
             200)
rkit_user = next((u for u in (body.get("users") or [])
                  if u.get("username") == "rkit_u1"), None)
if rkit_user and rkit_user.get("display_name") == "RKIT Software":
    PASS += 1
    print(" [PASS] users LEFT JOIN exposes display_name='RKIT Software'")
else:
    FAIL += 1
    print(" [FAIL] users join missing display_name: %s" % rkit_user)

# Rename the display label via PUT
body = check("PUT /admin/clients rename display_name",
             hit("PUT", "/admin/clients/%s" % rkit_id,
                 headers=H,
                 json={"display_name": "RKIT Software Pvt Ltd"}),
             200)
if body.get("display_name") == "RKIT Software Pvt Ltd":
    PASS += 1
    print(" [PASS] display_name renamed via PUT")
else:
    FAIL += 1
    print(" [FAIL] expected new display_name got: %s" % body)

# Clean up
hit("DELETE", "/admin/users/by-client/1056", headers=H)

# ─── 8. Standalone client delete (no users attached) ────────────
print("\n=== Standalone client delete ===")
body = check("POST /admin/clients second client (no users)",
             hit("POST", "/admin/clients",
                 headers=H,
                 json={"client_name": "Lonely", "ukey": "LONE1234"}),
             201)
lonely_id = body.get("id")

check("DELETE /admin/clients/<id> with no users",
      hit("DELETE", "/admin/clients/%s" % lonely_id, headers=H),
      200)

# ─── 9. Cascade delete by client (atomic: users + client) ───────
print("\n=== Cascade delete ===")
body = check("DELETE /admin/users/by-client/Acme (cascade)",
             hit("DELETE", "/admin/users/by-client/Acme", headers=H),
             200)
# Cascade endpoint also removes the empty client; confirm both happened
if body.get("deleted") == 1 and body.get("client_deleted") == 1:
    PASS += 1
    print(" [PASS] cascade removed 1 user AND the client atomically")
else:
    FAIL += 1
    print(" [FAIL] cascade response missing expected counts: %s" % body)

check("DELETE /admin/clients/<id> after cascade (already gone)",
      hit("DELETE", "/admin/clients/%s" % client_id, headers=H),
      404, "CLIENT_NOT_FOUND")

check("DELETE /admin/servers/<id> after users gone",
      hit("DELETE", "/admin/servers/%s" % server_id, headers=H),
      200)

# ─── 9. Partners CRUD (Phase v4.0.1) ────────────────────────────
print("\n=== Partners CRUD ===")

# Empty list
body = check("GET /admin/partners (empty)",
             hit("GET", "/admin/partners", headers=H),
             200)
if body.get("count") == 0:
    PASS += 1
    print(" [PASS] list starts empty")
else:
    FAIL += 1
    print(" [FAIL] expected count=0, got %s" % body)

# Missing required name
check("POST /admin/partners missing name",
      hit("POST", "/admin/partners", headers=H, json={"email": "a@b.co"}),
      400, "VALIDATION_FAILED")

# Bad email
check("POST /admin/partners bad email",
      hit("POST", "/admin/partners", headers=H,
          json={"name": "Bad Email Partner", "email": "not-an-email"}),
      400, "VALIDATION_FAILED")

# Bad phone
check("POST /admin/partners bad phone",
      hit("POST", "/admin/partners", headers=H,
          json={"name": "Bad Phone Partner", "phone": "abc"}),
      400, "VALIDATION_FAILED")

# Create (all three fields)
body = check("POST /admin/partners (full)",
             hit("POST", "/admin/partners", headers=H,
                 json={"name": "Acme Distribution",
                       "email": "contact@acme.example",
                       "phone": "+91-98765-43210"}),
             201, expect_keys=["id", "name", "email", "phone", "is_active"])
partner_id = body.get("id")
if body.get("is_active") == 1:
    PASS += 1
    print(" [PASS] is_active defaults to 1 on create")
else:
    FAIL += 1
    print(" [FAIL] expected is_active=1, got %s" % body.get("is_active"))

# Email is mandatory (v4.1c): name only -> 400
check("POST /admin/partners (name only -> email required)",
      hit("POST", "/admin/partners", headers=H,
          json={"name": "Solo Partner"}),
      400, "VALIDATION_FAILED")

# Create with email but no phone -> phone persists NULL
body = check("POST /admin/partners (email, no phone)",
             hit("POST", "/admin/partners", headers=H,
                 json={"name": "Solo Partner", "email": "solo@example.com"}),
             201)
solo_id = body.get("id")
if body.get("email") == "solo@example.com" and body.get("phone") is None:
    PASS += 1
    print(" [PASS] omitted phone persists as NULL (email required)")
else:
    FAIL += 1
    print(" [FAIL] expected NULL phone + email set, got %s" % body)

# Partner email accepts multiple comma-separated addresses (v4.2): lowercased,
# trimmed, de-duped (order kept), stored as "a@x.com,b@y.com".
body = check("POST /admin/partners (multi email)",
             hit("POST", "/admin/partners", headers=H,
                 json={"name": "Multi Mail Partner",
                       "email": "Sales@Acme.Example, support@acme.example , sales@acme.example"}),
             201)
if body.get("email") == "sales@acme.example,support@acme.example":
    PASS += 1; print(" [PASS] partner multi-email normalized + de-duped")
else:
    FAIL += 1; print(" [FAIL] partner multi-email wrong: %s" % body.get("email"))

# One bad address anywhere in the list rejects the whole value
check("POST /admin/partners (multi email, one invalid)",
      hit("POST", "/admin/partners", headers=H,
          json={"name": "Multi Bad Partner", "email": "ok@x.com, not-an-email"}),
      400, "VALIDATION_FAILED")

# Duplicate name (case-insensitive) — email present so it reaches the DB check
check("POST /admin/partners duplicate name (case-insensitive)",
      hit("POST", "/admin/partners", headers=H,
          json={"name": "acme DISTRIBUTION", "email": "dup@example.com"}),
      409, "PARTNER_NAME_EXISTS")

# Get by id
body = check("GET /admin/partners/<id>",
             hit("GET", "/admin/partners/%s" % partner_id, headers=H),
             200)
if body.get("name") == "Acme Distribution":
    PASS += 1
    print(" [PASS] get by id returns matching row")
else:
    FAIL += 1
    print(" [FAIL] name mismatch: %s" % body)

# 404
check("GET /admin/partners/<bogus>",
      hit("GET", "/admin/partners/99999", headers=H),
      404, "PARTNER_NOT_FOUND")

# Update: clear phone, rename email
body = check("PUT /admin/partners (clear phone, change email)",
             hit("PUT", "/admin/partners/%s" % partner_id, headers=H,
                 json={"email": "new@acme.example", "phone": ""}),
             200)
if body.get("phone") is None and body.get("email") == "new@acme.example":
    PASS += 1
    print(" [PASS] phone cleared to NULL, email changed")
else:
    FAIL += 1
    print(" [FAIL] update body: %s" % body)

# Deactivate
body = check("PUT /admin/partners is_active=0",
             hit("PUT", "/admin/partners/%s" % partner_id, headers=H,
                 json={"is_active": 0}),
             200)
if body.get("is_active") == 0:
    PASS += 1
    print(" [PASS] is_active flipped to 0")
else:
    FAIL += 1
    print(" [FAIL] is_active not updated: %s" % body)

# List with active_only=true (deactivated Acme should be filtered out)
body = check("GET /admin/partners?active_only=true",
             hit("GET", "/admin/partners?active_only=true", headers=H),
             200)
active_names = [p["name"] for p in body.get("partners", [])]
if "Acme Distribution" not in active_names and "Solo Partner" in active_names:
    PASS += 1
    print(" [PASS] active_only filters out deactivated partner")
else:
    FAIL += 1
    print(" [FAIL] active_only filter wrong: %s" % active_names)

# No fields to update
check("PUT /admin/partners empty body",
      hit("PUT", "/admin/partners/%s" % partner_id, headers=H, json={}),
      400, "NO_FIELDS_TO_UPDATE")

# Hard-delete Solo (no clients reference it)
body = check("DELETE /admin/partners/<id> (unreferenced -> hard)",
             hit("DELETE", "/admin/partners/%s" % solo_id, headers=H),
             200)
if body.get("deleted_kind") == "hard" and body.get("referenced_by") == 0:
    PASS += 1
    print(" [PASS] unreferenced partner hard-deleted")
else:
    FAIL += 1
    print(" [FAIL] expected hard delete: %s" % body)

# Confirm Solo is gone
check("GET /admin/partners/<hard-deleted>",
      hit("GET", "/admin/partners/%s" % solo_id, headers=H),
      404, "PARTNER_NOT_FOUND")

# Simulate a client referencing Acme, then soft-delete
import sqlite3 as _sqlite3
_c = _sqlite3.connect(TMP_DB)
_c.execute("INSERT INTO clients (client_name, ukey, partner_id) VALUES (?, ?, ?)",
           ("PartnerLinkTest", "PLNK1234", partner_id))
_c.commit()
_c.close()

body = check("DELETE /admin/partners/<id> (referenced -> soft)",
             hit("DELETE", "/admin/partners/%s" % partner_id, headers=H),
             200)
if body.get("deleted_kind") == "soft" and body.get("referenced_by") == 1:
    PASS += 1
    print(" [PASS] referenced partner soft-deleted (row preserved)")
else:
    FAIL += 1
    print(" [FAIL] expected soft delete: %s" % body)

# Row is still there, is_active=0
body = check("GET /admin/partners/<soft-deleted> (row preserved)",
             hit("GET", "/admin/partners/%s" % partner_id, headers=H),
             200)
if body.get("is_active") == 0:
    PASS += 1
    print(" [PASS] soft-deleted partner still readable with is_active=0")
else:
    FAIL += 1
    print(" [FAIL] soft-delete row check: %s" % body)

# Clean up the fake client so it doesn't inflate other counts
_c = _sqlite3.connect(TMP_DB)
_c.execute("DELETE FROM clients WHERE client_name = 'PartnerLinkTest'")
_c.commit()
_c.close()


# ─── 10. Phase 2: client account fields + user_type ─────────────
print("\n=== Phase 2: subscription dates + partner_id + user_type ===")

# Fresh server + active partner for this self-contained section
body = check("POST /admin/servers (phase2 server)",
             hit("POST", "/admin/servers", headers=H,
                 json={"server_name": "Phase2Svr", "server_ip": "10.0.2.2"}),
             201)
p2_server = body.get("id")

body = check("POST /admin/partners (phase2 partner)",
             hit("POST", "/admin/partners", headers=H,
                 json={"name": "Phase2 Partner", "email": "p2@example.com"}),
             201)
p2_partner = body.get("id")

# (a) v3.6-style three-field POST still succeeds + gets defaulted dates
import datetime as _dt
today_iso = _dt.date.today().isoformat()
body = check("POST /admin/clients (v3.6 three-field, back-compat)",
             hit("POST", "/admin/clients", headers=H,
                 json={"client_name": "LegacyStyle", "ukey": "LEGA1234"}),
             201)
if body.get("subscription_end"):
    PASS += 1
    print(" [PASS] omitted expiry -> auto-calculated (client-level)")
else:
    FAIL += 1
    print(" [FAIL] expected non-null subscription_end, got %s" % body)
if body.get("partner_id") is None and "partner_name" in body:
    PASS += 1
    print(" [PASS] response carries partner_id(null) + partner_name key")
else:
    FAIL += 1
    print(" [FAIL] partner fields missing on create response: %s" % body)

# (b) POST with explicit partner_id + subscription_start -> end auto-calc'd
body = check("POST /admin/clients (partner_id + start, end auto)",
             hit("POST", "/admin/clients", headers=H,
                 json={"client_name": "AcctFull", "ukey": "ACCT5678",
                       "partner_id": p2_partner,
                       "subscription_start": "2026-06-29"}),
             201)
acct_id = body.get("id")
if body.get("subscription_end") == "2027-06-28":
    PASS += 1
    print(" [PASS] subscription_end auto-calc = start + 1yr - 1day (2027-06-28)")
else:
    FAIL += 1
    print(" [FAIL] end auto-calc wrong: %s" % body)
if body.get("partner_id") == p2_partner and body.get("partner_name") == "Phase2 Partner":
    PASS += 1
    print(" [PASS] partner_id persisted + partner_name joined")
else:
    FAIL += 1
    print(" [FAIL] partner join wrong: %s" % body)

# (c) POST with explicit start AND end -> stored as sent (no auto-calc)
body = check("POST /admin/clients (explicit start + end)",
             hit("POST", "/admin/clients", headers=H,
                 json={"client_name": "AcctExplicit", "ukey": "ACCT9999",
                       "subscription_start": "2026-01-01",
                       "subscription_end": "2026-12-31"}),
             201)
if body.get("subscription_end") == "2026-12-31":
    PASS += 1
    print(" [PASS] explicit end stored as sent (not overwritten)")
else:
    FAIL += 1
    print(" [FAIL] explicit end wrong: %s" % body)

# (d) POST with bad partner_id -> 400 UNKNOWN_PARTNER
check("POST /admin/clients bad partner_id",
      hit("POST", "/admin/clients", headers=H,
          json={"client_name": "BadPartner", "ukey": "BADP1234", "partner_id": 99999}),
      400, "UNKNOWN_PARTNER")

# (e) POST with bad date -> 400 VALIDATION_FAILED
check("POST /admin/clients bad date format",
      hit("POST", "/admin/clients", headers=H,
          json={"client_name": "BadDate", "ukey": "BADD1234",
                "subscription_start": "29-06-2026"}),
      400, "VALIDATION_FAILED")

check("POST /admin/clients impossible date",
      hit("POST", "/admin/clients", headers=H,
          json={"client_name": "BadDate2", "ukey": "BADD5678",
                "subscription_start": "2026-13-40"}),
      400, "VALIDATION_FAILED")

# (f) PUT changes partner_id + both dates
body = check("PUT /admin/clients change partner + dates",
             hit("PUT", "/admin/clients/%s" % acct_id, headers=H,
                 json={"partner_id": p2_partner,
                       "subscription_start": "2027-03-01",
                       "subscription_end": "2028-02-28"}),
             200)
if (body.get("subscription_end") == "2028-02-28"
        and body.get("partner_id") == p2_partner):
    PASS += 1
    print(" [PASS] PUT updated partner_id + expiry")
else:
    FAIL += 1
    print(" [FAIL] PUT update wrong: %s" % body)

# (g) PUT new start without end -> auto-calc end
body = check("PUT /admin/clients new start, end auto",
             hit("PUT", "/admin/clients/%s" % acct_id, headers=H,
                 json={"subscription_start": "2029-06-29"}),
             200)
if body.get("subscription_end") == "2030-06-28":
    PASS += 1
    print(" [PASS] PUT auto-calc end when only start sent")
else:
    FAIL += 1
    print(" [FAIL] PUT auto-calc wrong: %s" % body)

# (h) PUT bad partner_id -> 400
check("PUT /admin/clients bad partner_id",
      hit("PUT", "/admin/clients/%s" % acct_id, headers=H,
          json={"partner_id": 88888}),
      400, "UNKNOWN_PARTNER")

# (i) GET client returns partner + subscription fields
body = check("GET /admin/clients/<id> (Phase 2 fields)",
             hit("GET", "/admin/clients/%s" % acct_id, headers=H),
             200)
if all(k in body for k in ("partner_id", "partner_name", "subscription_type", "storage_gb", "subscription_end")):
    PASS += 1
    print(" [PASS] GET client exposes partner + subscription_type/storage_gb/expiry")
else:
    FAIL += 1
    print(" [FAIL] GET client missing account fields: %s" % list(body.keys()))

# (j) GET list carries Phase 2 fields
body = check("GET /admin/clients (list has Phase 2 fields)",
             hit("GET", "/admin/clients", headers=H),
             200)
sample = body.get("clients", [{}])[0]
if all(k in sample for k in ("partner_id", "partner_name", "subscription_type", "storage_gb", "subscription_end")):
    PASS += 1
    print(" [PASS] list rows carry account fields")
else:
    FAIL += 1
    print(" [FAIL] list rows missing account fields: %s" % list(sample.keys()))

# (k) users.user_type: default 'new' when omitted
body = check("POST /admin/users (user_type omitted -> new)",
             hit("POST", "/admin/users", headers=H,
                 json={"username": "acct_admin1", "client_name": "AcctFull",
                       "server_id": p2_server}),
             201)
if body.get("user_type") == "new":
    PASS += 1
    print(" [PASS] user_type defaults to 'new'")
else:
    FAIL += 1
    print(" [FAIL] expected user_type='new', got %s" % body.get("user_type"))

# (l) explicit user_type='additional' stored
body = check("POST /admin/users (user_type=additional)",
             hit("POST", "/admin/users", headers=H,
                 json={"username": "acct_extra1", "client_name": "AcctFull",
                       "server_id": p2_server, "user_type": "additional"}),
             201)
if body.get("user_type") == "additional":
    PASS += 1
    print(" [PASS] user_type='additional' stored")
else:
    FAIL += 1
    print(" [FAIL] expected 'additional', got %s" % body.get("user_type"))

# (m) invalid user_type -> 400
check("POST /admin/users bad user_type",
      hit("POST", "/admin/users", headers=H,
          json={"username": "acct_bad1", "client_name": "AcctFull",
                "server_id": p2_server, "user_type": "banana"}),
      400, "VALIDATION_FAILED")

# (n) GET /admin/users?expand=true carries per-user report fields
body = check("GET /admin/users?expand=true (report fields per row)",
             hit("GET", "/admin/users?expand=true&client_name=AcctFull", headers=H),
             200)
rows = body.get("users", [])
report_keys = ("user_type", "partner_id", "partner_name",
               "subscription_end", "subscription_start", "display_name", "ukey")
if rows and all(all(k in r for k in report_keys) for r in rows):
    PASS += 1
    print(" [PASS] every user row carries the full report field set")
else:
    FAIL += 1
    print(" [FAIL] user rows missing report fields: %s"
          % (list(rows[0].keys()) if rows else "no rows"))
# and the joined partner_name actually resolved
if rows and rows[0].get("partner_name") == "Phase2 Partner":
    PASS += 1
    print(" [PASS] partner_name resolves via user->client->partner join")
else:
    FAIL += 1
    print(" [FAIL] partner_name join on user row wrong: %s"
          % (rows[0].get("partner_name") if rows else "no rows"))


# ─── 10b. Phase 4.1a: subscription_type + storage_gb + user subscription_start ──
print("\n=== Phase 4.1a: subscription_type / storage_gb / user subscription_start ===")

# Client with subscription_type + storage_gb persists + returns them
body = check("POST /admin/clients (subscription_type + storage_gb)",
             hit("POST", "/admin/clients", headers=H,
                 json={"client_name": "AcctV41", "ukey": "V41A1234",
                       "subscription_type": "multi", "storage_gb": 5}),
             201)
v41_id = body.get("id")
if body.get("subscription_type") == "multi" and body.get("storage_gb") == 5:
    PASS += 1
    print(" [PASS] subscription_type + storage_gb persisted")
else:
    FAIL += 1
    print(" [FAIL] account fields wrong: %s" % body)

# Case-insensitive subscription_type
body = check("POST /admin/clients (subscription_type SINGLE upper)",
             hit("POST", "/admin/clients", headers=H,
                 json={"client_name": "AcctV41b", "ukey": "V41B1234",
                       "subscription_type": "SINGLE"}),
             201)
if body.get("subscription_type") == "single":
    PASS += 1
    print(" [PASS] subscription_type normalized to lowercase")
else:
    FAIL += 1
    print(" [FAIL] expected 'single', got %s" % body.get("subscription_type"))

# Invalid subscription_type -> 400
check("POST /admin/clients bad subscription_type",
      hit("POST", "/admin/clients", headers=H,
          json={"client_name": "BadSub", "ukey": "BSUB1234",
                "subscription_type": "enterprise"}),
      400, "VALIDATION_FAILED")

# Invalid storage_gb (zero) -> 400
check("POST /admin/clients storage_gb=0",
      hit("POST", "/admin/clients", headers=H,
          json={"client_name": "BadStor", "ukey": "BSTO1234", "storage_gb": 0}),
      400, "VALIDATION_FAILED")

# Invalid storage_gb (non-int) -> 400
check("POST /admin/clients storage_gb non-int",
      hit("POST", "/admin/clients", headers=H,
          json={"client_name": "BadStor2", "ukey": "BSTO5678", "storage_gb": "lots"}),
      400, "VALIDATION_FAILED")

# PUT can edit subscription_type + storage_gb
body = check("PUT /admin/clients (edit type + storage)",
             hit("PUT", "/admin/clients/%s" % v41_id, headers=H,
                 json={"subscription_type": "single", "storage_gb": 20}),
             200)
if body.get("subscription_type") == "single" and body.get("storage_gb") == 20:
    PASS += 1
    print(" [PASS] PUT edited subscription_type + storage_gb")
else:
    FAIL += 1
    print(" [FAIL] PUT edit wrong: %s" % body)

# User with explicit subscription_start persists + returns it
body = check("POST /admin/users (explicit subscription_start)",
             hit("POST", "/admin/users", headers=H,
                 json={"username": "v41_user1", "client_name": "AcctV41",
                       "server_id": p2_server, "user_type": "new",
                       "subscription_start": "2026-03-15"}),
             201)
if body.get("subscription_start") == "2026-03-15":
    PASS += 1
    print(" [PASS] explicit user subscription_start persisted")
else:
    FAIL += 1
    print(" [FAIL] expected subscription_start=2026-03-15, got %s" % body.get("subscription_start"))

# User without subscription_start -> defaults to today
body = check("POST /admin/users (subscription_start omitted -> today)",
             hit("POST", "/admin/users", headers=H,
                 json={"username": "v41_user2", "client_name": "AcctV41",
                       "server_id": p2_server}),
             201)
if body.get("subscription_start") == today_iso:
    PASS += 1
    print(" [PASS] omitted subscription_start defaults to today")
else:
    FAIL += 1
    print(" [FAIL] expected subscription_start=%s, got %s" % (today_iso, body.get("subscription_start")))

# Invalid subscription_start -> 400
check("POST /admin/users bad subscription_start",
      hit("POST", "/admin/users", headers=H,
          json={"username": "v41_bad", "client_name": "AcctV41",
                "server_id": p2_server, "subscription_start": "15-03-2026"}),
      400, "VALIDATION_FAILED")

# GET /admin/users?expand=true rows carry subscription_start + subscription_type + storage_gb
body = check("GET /admin/users?expand=true (v4.1a fields per row)",
             hit("GET", "/admin/users?expand=true&client_name=AcctV41", headers=H),
             200)
rows = body.get("users", [])
v41_keys = ("subscription_start", "subscription_type", "storage_gb", "user_type")
if rows and all(all(k in r for k in v41_keys) for r in rows):
    PASS += 1
    print(" [PASS] user rows carry subscription_start + subscription_type + storage_gb")
else:
    FAIL += 1
    print(" [FAIL] user rows missing v4.1a fields: %s"
          % (list(rows[0].keys()) if rows else "no rows"))


# ─── 10c. Phase 4.1b: grouped report (GET /admin/users default) ──
print("\n=== Phase 4.1b: grouped User-wise Report ===")

# Dedicated account with a partner + known user batches
body = check("POST /admin/clients (RptAcct)",
             hit("POST", "/admin/clients", headers=H,
                 json={"client_name": "RptAcct", "ukey": "RPT01234",
                       "partner_id": p2_partner, "subscription_type": "multi",
                       "storage_gb": 10}),
             201)
rpt_client_id = body.get("id")

def _mkuser(uname, utype, sdate):
    return hit("POST", "/admin/users", headers=H,
               json={"username": uname, "client_name": "RptAcct",
                     "server_id": p2_server, "user_type": utype,
                     "subscription_start": sdate})

# New batch: 3 users @ 2026-01-01
_mkuser("rpt_new1", "new", "2026-01-01")
_mkuser("rpt_new2", "new", "2026-01-01")
b = _mkuser("rpt_new3", "new", "2026-01-01"); rpt_new3_id = json.loads(b.data.decode())["id"]
# Additional batch (Jul): 2 users @ 2026-07-15
_mkuser("rpt_add1", "additional", "2026-07-15")
_mkuser("rpt_add2", "additional", "2026-07-15")
# Additional batch (Aug): 1 user @ 2026-08-20
_mkuser("rpt_add3", "additional", "2026-08-20")
# Deactivate one New user -> tests active/inactive split + 'mixed' badge
hit("POST", "/admin/users/%s/disable" % rpt_new3_id, headers=H)

# Grouped default, scoped to RptAcct
body = check("GET /admin/users (grouped, client=RptAcct)",
             hit("GET", "/admin/users?client_name=RptAcct", headers=H),
             200, expect_keys=["summary", "rows", "count"])
rows = body.get("rows", [])
summ = body.get("summary", {})

if len(rows) == 3:
    PASS += 1; print(" [PASS] 3 batch rows (1 New + 2 Additional dates)")
else:
    FAIL += 1; print(" [FAIL] expected 3 rows, got %d" % len(rows))

if (summ.get("total_customer_ids") == 1 and summ.get("total_users") == 6
        and summ.get("active_users") == 5 and summ.get("deactive_users") == 1):
    PASS += 1; print(" [PASS] summary cards: 1 customer, 6 users, 5 active, 1 deactive")
else:
    FAIL += 1; print(" [FAIL] summary wrong: %s" % summ)

if rows and all(r.get("total_users") == 6 for r in rows):
    PASS += 1; print(" [PASS] total_users = client grand total (6) on every row")
else:
    FAIL += 1; print(" [FAIL] total_users wrong: %s" % [r.get("total_users") for r in rows])

# grouped rows carry the CLIENT id (for the inline End-Date edit)
if rows and all(r.get("id") == rpt_client_id for r in rows):
    PASS += 1; print(" [PASS] grouped rows carry client id == %s" % rpt_client_id)
else:
    FAIL += 1; print(" [FAIL] grouped row id wrong: %s" % [r.get("id") for r in rows])

new_row = next((r for r in rows if r["user_type"] == "new"), None)
if new_row and new_row["no_of_users"] == 3 and new_row["active_users"] == 2 \
        and new_row["inactive_users"] == 1 and new_row["status"] == "mixed":
    PASS += 1; print(" [PASS] New row: 3 users, 2 active / 1 inactive, status=mixed")
else:
    FAIL += 1; print(" [FAIL] New row wrong: %s" % new_row)

add_counts = sorted(r["no_of_users"] for r in rows if r["user_type"] == "additional")
if add_counts == [1, 2]:
    PASS += 1; print(" [PASS] Additional rows have counts 1 and 2")
else:
    FAIL += 1; print(" [FAIL] additional counts wrong: %s" % add_counts)

# expand=true -> per-user rows
body = check("GET /admin/users?expand=true (RptAcct users)",
             hit("GET", "/admin/users?expand=true&client_name=RptAcct", headers=H),
             200, expect_keys=["users", "count"])
urows = body.get("users", [])
if body.get("count") == 6:
    PASS += 1; print(" [PASS] expand=true returns 6 per-user rows")
else:
    FAIL += 1; print(" [FAIL] expected 6 users, got %s" % body.get("count"))
# per-user rows carry client_id (the client id) alongside id (the user id)
if urows and all(u.get("client_id") == rpt_client_id for u in urows) \
        and all("id" in u for u in urows):
    PASS += 1; print(" [PASS] expand rows carry client_id (%s) alongside per-user id" % rpt_client_id)
else:
    FAIL += 1; print(" [FAIL] expand client_id wrong: %s"
                     % [(u.get("id"), u.get("client_id")) for u in urows])

# Filter: user_type=additional -> 2 rows, 3 users total
body = check("GET /admin/users (grouped, user_type=additional)",
             hit("GET", "/admin/users?client_name=RptAcct&user_type=additional", headers=H),
             200)
rows = body.get("rows", [])
if len(rows) == 2 and sum(r["no_of_users"] for r in rows) == 3:
    PASS += 1; print(" [PASS] user_type filter -> 2 additional rows, 3 users")
else:
    FAIL += 1; print(" [FAIL] user_type filter wrong: %s" % rows)

# Filter: status=active -> New row now counts only 2 active
body = check("GET /admin/users (grouped, status=active)",
             hit("GET", "/admin/users?client_name=RptAcct&status=active", headers=H),
             200)
if body.get("summary", {}).get("total_users") == 5:
    PASS += 1; print(" [PASS] status=active summary counts 5 active users")
else:
    FAIL += 1; print(" [FAIL] status=active summary wrong: %s" % body.get("summary"))

# Filter: partner name substring
body = check("GET /admin/users (grouped, partner=Phase2)",
             hit("GET", "/admin/users?client_name=RptAcct&partner=Phase2", headers=H),
             200)
n_match = len(body.get("rows", []))
body2 = check("GET /admin/users (grouped, partner=NoSuchPartner)",
              hit("GET", "/admin/users?client_name=RptAcct&partner=NoSuchPartner", headers=H),
              200)
if n_match == 3 and len(body2.get("rows", [])) == 0:
    PASS += 1; print(" [PASS] partner filter matches (3) / non-match (0)")
else:
    FAIL += 1; print(" [FAIL] partner filter wrong: match=%d nomatch=%d"
                     % (n_match, len(body2.get("rows", []))))

# Global search by Customer ID substring
body = check("GET /admin/users (grouped, search=RptAcct)",
             hit("GET", "/admin/users?search=RptAcct", headers=H),
             200)
srows = body.get("rows", [])
if srows and all(r["client_name"] == "RptAcct" for r in srows) and len(srows) == 3:
    PASS += 1; print(" [PASS] search=RptAcct isolates the 3 RptAcct rows")
else:
    FAIL += 1; print(" [FAIL] search wrong: %s" % [r.get("client_name") for r in srows])

# Unscoped grouped default returns summary + rows envelope
body = check("GET /admin/users (grouped, unscoped envelope)",
             hit("GET", "/admin/users", headers=H),
             200, expect_keys=["summary", "rows", "count"])
if body.get("count", 0) >= 3:
    PASS += 1; print(" [PASS] unscoped grouped default returns envelope")
else:
    FAIL += 1; print(" [FAIL] unscoped grouped wrong: count=%s" % body.get("count"))


# ─── 10d. Phase 4.1c: partner + partner-email mandatory ──────────
print("\n=== Phase 4.1c: partner + email mandatory ===")

# Enforcement ON
config.REQUIRE_PARTNER = True
check("POST /admin/clients (no partner -> 400 when required)",
      hit("POST", "/admin/clients", headers=H,
          json={"client_name": "NeedPartner", "ukey": "NEED1234"}),
      400, "VALIDATION_FAILED")

body = check("POST /admin/clients (with partner -> 201 when required)",
             hit("POST", "/admin/clients", headers=H,
                 json={"client_name": "HasPartner", "ukey": "HASP1234",
                       "partner_id": p2_partner}),
             201)
if body.get("partner_id") == p2_partner:
    PASS += 1; print(" [PASS] client with partner created under enforcement")
else:
    FAIL += 1; print(" [FAIL] partner not set: %s" % body)

# Relax again so any later additions don't need a partner
config.REQUIRE_PARTNER = False
check("POST /admin/clients (no partner -> 201 when relaxed)",
      hit("POST", "/admin/clients", headers=H,
          json={"client_name": "NoPartnerOK", "ukey": "NOPA1234"}),
      201)

# Partner email is mandatory: create without email -> 400
check("POST /admin/partners (missing email -> 400)",
      hit("POST", "/admin/partners", headers=H,
          json={"name": "NoEmail Partner"}),
      400, "VALIDATION_FAILED")

# Partner email cannot be cleared via PUT -> 400
check("PUT /admin/partners (clear email -> 400)",
      hit("PUT", "/admin/partners/%s" % p2_partner, headers=H,
          json={"email": ""}),
      400, "VALIDATION_FAILED")

# But changing email to a new valid value is fine
body = check("PUT /admin/partners (change email -> 200)",
             hit("PUT", "/admin/partners/%s" % p2_partner, headers=H,
                 json={"email": "phase2-new@example.com"}),
             200)
if body.get("email") == "phase2-new@example.com":
    PASS += 1; print(" [PASS] partner email changed to a new valid value")
else:
    FAIL += 1; print(" [FAIL] partner email change wrong: %s" % body)


# ─── 10e. Client-level contact fields (v4.1 migration plumbing) ──
print("\n=== Client-level contact (contact_email / contact_mobile) ===")

body = check("POST /admin/clients (with contact)",
             hit("POST", "/admin/clients", headers=H,
                 json={"client_name": "ContactAcct", "ukey": "CTCT1234",
                       "contact_email": "BILL@Acct.Example",
                       "contact_mobile": "+91 98765-11111"}),
             201)
ct_id = body.get("id")
if body.get("contact_email") == "bill@acct.example" and body.get("contact_mobile") == "+919876511111":
    PASS += 1; print(" [PASS] contact persisted (email lowercased, mobile stripped)")
else:
    FAIL += 1; print(" [FAIL] contact wrong: %s" % body)

check("POST /admin/clients bad contact_email",
      hit("POST", "/admin/clients", headers=H,
          json={"client_name": "BadCE", "ukey": "BADC1234", "contact_email": "not-an-email"}),
      400, "VALIDATION_FAILED")

check("POST /admin/clients bad contact_mobile",
      hit("POST", "/admin/clients", headers=H,
          json={"client_name": "BadCM", "ukey": "BADM1234", "contact_mobile": "abc"}),
      400, "VALIDATION_FAILED")

body = check("GET /admin/clients/<id> returns contact",
             hit("GET", "/admin/clients/%s" % ct_id, headers=H), 200)
if body.get("contact_email") == "bill@acct.example":
    PASS += 1; print(" [PASS] GET client exposes contact_email")
else:
    FAIL += 1; print(" [FAIL] GET contact missing: %s" % list(body.keys()))

body = check("PUT /admin/clients edit contact",
             hit("PUT", "/admin/clients/%s" % ct_id, headers=H,
                 json={"contact_email": "new@acct.example"}), 200)
if body.get("contact_email") == "new@acct.example":
    PASS += 1; print(" [PASS] PUT updated contact_email")
else:
    FAIL += 1; print(" [FAIL] PUT contact wrong: %s" % body)

# Multi-address contact_email: comma-separated -> lowercased, trimmed,
# de-duped (order kept), stored as "a@x.com,b@y.com" (no spaces).
body = check("POST /admin/clients (multi contact_email)",
             hit("POST", "/admin/clients", headers=H,
                 json={"client_name": "MultiMail", "ukey": "MULT1234",
                       "contact_email": "Owner@Acct.Example, billing@acct.example , owner@acct.example"}),
             201)
if body.get("contact_email") == "owner@acct.example,billing@acct.example":
    PASS += 1; print(" [PASS] multi contact_email normalized + de-duped")
else:
    FAIL += 1; print(" [FAIL] multi contact_email wrong: %s" % body.get("contact_email"))

# One bad address anywhere in the list rejects the whole value
check("POST /admin/clients (multi contact_email, one invalid)",
      hit("POST", "/admin/clients", headers=H,
          json={"client_name": "MultiBad", "ukey": "MULB1234",
                "contact_email": "good@x.com, not-an-email"}),
      400, "VALIDATION_FAILED")

body = check("GET /admin/clients (list has contact fields)",
             hit("GET", "/admin/clients", headers=H), 200)
sample = body.get("clients", [{}])[0]
if "contact_email" in sample and "contact_mobile" in sample:
    PASS += 1; print(" [PASS] list rows carry contact fields")
else:
    FAIL += 1; print(" [FAIL] list missing contact fields: %s" % list(sample.keys()))


# ─── 10e. Per-user email / mobile REMOVED (v4.3) ──────────────────
# Contacts are account-level only: clients.contact_email / contact_mobile.
# Sending either key to /admin/users is rejected outright rather than
# silently ignored, so an old EXE cannot believe it stored a contact.
print("\n=== Per-user email/mobile removed (account-level only) ===")

check("POST /admin/clients (OptCT host)",
      hit("POST", "/admin/clients", headers=H,
          json={"client_name": "OptCT", "ukey": "OPTC1234",
                "contact_email": "Acct@Opt.Example, ops@opt.example",
                "contact_mobile": "+919876543210, +919876543211"}), 201)

def _optuser(extra):
    payload = {"client_name": "OptCT", "server_id": p2_server}
    payload.update(extra)
    return hit("POST", "/admin/users", headers=H, json=payload)

# clean create (no contact keys) -> 201, and no email/mobile on the row
body = check("POST user (no contact keys)", _optuser({"username": "opt_none"}), 201)
opt_id = body.get("id")
if "email" not in body and "mobile" not in body:
    PASS += 1; print(" [PASS] user row carries no per-user email/mobile")
else:
    FAIL += 1; print(" [FAIL] user row still exposes email/mobile: %s" % body)

# the account contact rides along on the user row via the client join
if (body.get("contact_email") == "acct@opt.example,ops@opt.example"
        and body.get("contact_mobile") == "+919876543210,+919876543211"):
    PASS += 1; print(" [PASS] account contact_email/contact_mobile on the user row")
else:
    FAIL += 1; print(" [FAIL] contact fields wrong: %s / %s"
                     % (body.get("contact_email"), body.get("contact_mobile")))

# sending either key -> 400, naming the replacement
for key, val in (("email", "x@y.com"), ("mobile", "+919999999999")):
    body = check("POST user (%s rejected)" % key,
                 _optuser({"username": "opt_%s" % key, key: val}),
                 400, "VALIDATION_FAILED")
    det = " ".join(body.get("details") or [])
    if "contact_email" in det and "contact_mobile" in det:
        PASS += 1; print(" [PASS] %s rejection points at the account-level fields" % key)
    else:
        FAIL += 1; print(" [FAIL] unhelpful message: %r" % det)

# same on PUT
check("PUT user (email rejected)",
      hit("PUT", "/admin/users/%s" % opt_id, headers=H, json={"email": ""}),
      400, "VALIDATION_FAILED")

# still-required fields missing -> 400
check("POST user (missing username/client/server)",
      hit("POST", "/admin/users", headers=H, json={}),
      400, "VALIDATION_FAILED")

# contact_mobile is multi-value now (v4.3) -- and single still works
body = check("POST /admin/clients (single contact_mobile still OK)",
             hit("POST", "/admin/clients", headers=H,
                 json={"client_name": "OneMob", "ukey": "ONEMOB12",
                       "contact_mobile": "+919876500000"}), 201)
if body.get("contact_mobile") == "+919876500000":
    PASS += 1; print(" [PASS] single contact_mobile unchanged (back-compat)")
else:
    FAIL += 1; print(" [FAIL] got %r" % body.get("contact_mobile"))

check("POST /admin/clients (bad number in contact_mobile list)",
      hit("POST", "/admin/clients", headers=H,
          json={"client_name": "BadMob", "ukey": "BADMOB12",
                "contact_mobile": "+919876500000,nope"}), 400, "VALIDATION_FAILED")


# --- 10f. v4.3: legacy migration (user_type='migrated' + provenance) ---
print("\n=== v4.3: legacy migration ===")

body = check("POST /admin/clients (migrated, legacy + back-dated start)",
             hit("POST", "/admin/clients", headers=H,
                 json={"client_name": "MigCT", "display_name": "Migrated Co",
                       "ukey": "MIGC1234",
                       "legacy_server_name": "moc1",
                       "legacy_server_ip": "10.0.0.14",
                       "subscription_start": "2025-12-01",
                       "subscription_end": "2026-11-30"}), 201)
mig_client_id = body.get("id")
# subscription_start is per-USER now: the client must NOT store it, but the
# back-dated value still drives the shared expiry.
if (body.get("legacy_server_name") == "moc1"
        and body.get("legacy_server_ip") == "10.0.0.14"
        and body.get("subscription_start") is None
        and body.get("subscription_end") == "2026-11-30"):
    PASS += 1; print(" [PASS] legacy_* stored; client does NOT store subscription_start")
else:
    FAIL += 1; print(" [FAIL] legacy/start wrong: %s" % body)

body = check("POST /admin/users (user_type=migrated)",
             hit("POST", "/admin/users", headers=H,
                 json={"username": "MigCT_admin1", "client_name": "MigCT",
                       "server_id": p2_server, "user_type": "migrated",
                       "subscription_start": "2025-12-01"}), 201)
if body.get("user_type") == "migrated" and body.get("subscription_start") == "2025-12-01":
    PASS += 1; print(" [PASS] user_type='migrated' + back-dated subscription_start stored")
else:
    FAIL += 1; print(" [FAIL] expected migrated/2025-12-01, got %s/%s"
                     % (body.get("user_type"), body.get("subscription_start")))

# server_id must be the CURRENT target; legacy_* is provenance only.
if body.get("server_id") == p2_server and body.get("legacy_server_name") == "moc1":
    PASS += 1; print(" [PASS] server_id = current server; legacy_* joined from client")
else:
    FAIL += 1; print(" [FAIL] routing/provenance mixed up: %s" % body)

hit("POST", "/admin/users", headers=H,
    json={"username": "MigCT_user2", "client_name": "MigCT",
          "server_id": p2_server, "user_type": "additional",
          "subscription_start": "2026-08-04"})

body = check("POST /admin/users (user_type='MIGRATED')",
             hit("POST", "/admin/users", headers=H,
                 json={"username": "MigCT_case", "client_name": "MigCT",
                       "server_id": p2_server, "user_type": "MIGRATED"}), 201)
if body.get("user_type") == "migrated":
    PASS += 1; print(" [PASS] 'MIGRATED' normalized to 'migrated'")
else:
    FAIL += 1; print(" [FAIL] expected 'migrated', got %s" % body.get("user_type"))

body = check("GET /admin/users?user_type=migrated",
             hit("GET", "/admin/users?client_name=MigCT&user_type=migrated", headers=H), 200)
rows = body.get("rows", [])
if len(rows) == 2 and all(r["user_type"] == "migrated" for r in rows):
    PASS += 1; print(" [PASS] user_type=migrated filter returns only migrated batches")
else:
    FAIL += 1; print(" [FAIL] migrated filter wrong: %s" % rows)

body = check("GET /admin/users?client_name=MigCT",
             hit("GET", "/admin/users?client_name=MigCT", headers=H), 200)
summ = body.get("summary", {})
types = sorted({r["user_type"] for r in body.get("rows", [])})
if types == ["additional", "migrated"]:
    PASS += 1; print(" [PASS] migrated + additional are separate batches")
else:
    FAIL += 1; print(" [FAIL] expected both batch types, got %s" % types)

if (summ.get("migrated_users") == 2 and summ.get("additional_users") == 1
        and summ.get("new_users") == 0
        and summ.get("new_users") + summ.get("additional_users")
            + summ.get("migrated_users") == summ.get("total_users")):
    PASS += 1; print(" [PASS] summary splits 2 migrated / 1 additional / 0 new, sums to total")
else:
    FAIL += 1; print(" [FAIL] summary split wrong: %s" % summ)

# Write-once: PUT must not overwrite legacy_*; a real PUT bumps updated_at.
hit("PUT", "/admin/clients/%s" % mig_client_id, headers=H,
    json={"display_name": "Migrated Co Ltd"})
body = check("GET /admin/clients/exists/MigCT (after PUT)",
             hit("GET", "/admin/clients/exists/MigCT", headers=H), 200)
if (body.get("legacy_server_name") == "moc1"
        and body.get("legacy_server_ip") == "10.0.0.14"
        and body.get("updated_at") is not None):
    PASS += 1; print(" [PASS] write-once: legacy_* survived PUT; clients.updated_at bumped")
else:
    FAIL += 1; print(" [FAIL] write-once violated: %s" % body)

body = check("POST /admin/users (user_type=banana)",
             hit("POST", "/admin/users", headers=H,
                 json={"username": "MigCT_bad", "client_name": "MigCT",
                       "server_id": p2_server, "user_type": "banana"}),
             400, "VALIDATION_FAILED")
det = " ".join(body.get("details") or [])
if all(v in det for v in ("'new'", "'additional'", "'migrated'")):
    PASS += 1; print(" [PASS] error message names all three valid user_type values")
else:
    FAIL += 1; print(" [FAIL] message missing a value: %r" % det)

check("POST /admin/clients (bad legacy_server_ip octet)",
      hit("POST", "/admin/clients", headers=H,
          json={"client_name": "MigBad", "ukey": "MIGB1234",
                "legacy_server_ip": "10.0.0.999"}), 400, "VALIDATION_FAILED")

body = check("POST /admin/clients (pre-v4.3 body shape)",
             hit("POST", "/admin/clients", headers=H,
                 json={"client_name": "PlainCT", "display_name": "Plain",
                       "ukey": "PLAIN123"}), 201)
if (body.get("legacy_server_name") is None and body.get("legacy_server_ip") is None
        and body.get("updated_at") is None):
    PASS += 1; print(" [PASS] absent legacy_* -> null; updated_at null until first PUT")
else:
    FAIL += 1; print(" [FAIL] expected nulls, got %s" % body)


# ─── 11. Verify request_log captured everything ─────────────────
print("\n=== request_log audit ===")
import sqlite3
conn = sqlite3.connect(TMP_DB)
rows = conn.execute(
    "SELECT method, path, status, duration_ms IS NOT NULL FROM request_log ORDER BY id"
).fetchall()
conn.close()
print("       request_log row count: %d" % len(rows))
if len(rows) > 20:
    PASS += 1
    print(" [PASS] request_log captured >20 rows")
    # Sample first 3
    for r in rows[:3]:
        print("         e.g.", r)
else:
    FAIL += 1
    print(" [FAIL] request_log under-captured (%d rows)" % len(rows))

# ─── Summary ────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(" PASS: %d   FAIL: %d" % (PASS, FAIL))
print("=" * 60)
sys.exit(0 if FAIL == 0 else 1)
