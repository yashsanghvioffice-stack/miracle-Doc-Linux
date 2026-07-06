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
                     "email": "u1@acme.example",
                     "mobile": "+15551234567",
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
              "email": "u1b@acme.example",
              "mobile": "+15559999999",
              "server_id": server_id,
          }),
      409, "USERNAME_EXISTS")

check("POST /admin/users unknown client",
      hit("POST", "/admin/users",
          headers=H,
          json={
              "username": "ghost_user",
              "client_name": "NoSuchClient",
              "email": "g@x.com",
              "mobile": "+15550000000",
              "server_id": server_id,
          }),
      400, "UNKNOWN_CLIENT")

check("POST /admin/users unknown server",
      hit("POST", "/admin/users",
          headers=H,
          json={
              "username": "ghost_user2",
              "client_name": "Acme",
              "email": "g@x.com",
              "mobile": "+15550000000",
              "server_id": 99999,
          }),
      400, "UNKNOWN_SERVER")

check("POST /admin/users validation: bad email",
      hit("POST", "/admin/users",
          headers=H,
          json={
              "username": "badmail",
              "client_name": "Acme",
              "email": "not-an-email",
              "mobile": "+15550000000",
              "server_id": server_id,
          }),
      400, "VALIDATION_FAILED")

body = check("GET /admin/users (includes ukey)",
             hit("GET", "/admin/users", headers=H),
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
          "email": "u1@rkit.example", "mobile": "+15550000001",
          "server_id": server_id})
body = check("GET /admin/users carries display_name via JOIN",
             hit("GET", "/admin/users", headers=H),
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

# Create (name only — email/phone omitted store NULL)
body = check("POST /admin/partners (name only)",
             hit("POST", "/admin/partners", headers=H,
                 json={"name": "Solo Partner"}),
             201)
solo_id = body.get("id")
if body.get("email") is None and body.get("phone") is None:
    PASS += 1
    print(" [PASS] omitted email/phone persist as NULL")
else:
    FAIL += 1
    print(" [FAIL] expected NULL email/phone, got %s" % body)

# Duplicate name (case-insensitive)
check("POST /admin/partners duplicate name (case-insensitive)",
      hit("POST", "/admin/partners", headers=H,
          json={"name": "acme DISTRIBUTION"}),
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


# ─── 10. Verify request_log captured everything ─────────────────
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
