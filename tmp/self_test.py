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

# ─── 9. Verify request_log captured everything ──────────────────
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
