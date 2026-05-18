#!/usr/bin/env python3
"""
Miracle Cloud Gateway - Router API (v3.5)

CHANGES vs v3.4 (additive -- uKey edition)
    - NEW: clients table required (see migrate_v6.py).
           Holds (client_name, ukey) -- one row per tenant.
    - NEW: /login now requires a `ukey` form/JSON field.
           Strict bind: (username, ukey) must match the same row when
           users is joined with clients on client_name. A bad uKey fails
           fast (INVALID_UKEY) BEFORE TSplus is called. Same for both
           preference=html5 and preference=remote.
    - NEW: miracle_ukey cookie set on login success (30 days, JS-readable,
           SameSite=Lax). Used by the login page to recover the uKey
           after logout and across reloads. Coexists with all 16 TSplus
           fingerprint cookies -- displaces nothing.
    - NEW: /logout preserves miracle_ukey cookie and redirects to
           /?uKey=<value> instead of /.
    - NEW: /admin/clients/* CRUD (list, create, get-by-id, get-by-name,
           exists, update, delete).
    - CHANGED: POST /admin/users now requires the client_name to exist
               in the clients table -- returns 400 UNKNOWN_CLIENT
               otherwise. Same for PUT when client_name is sent.
    - CHANGED: DELETE /admin/users/by-client/<name> now also deletes
               the matching clients row (cascade). Response gains
               `client_deleted` and `ukey` fields. Still 200 even when
               nothing existed.
    - CHANGED: /health and /admin/stats add total_clients.

UNCHANGED FROM v3.4
    - TSplus fingerprint cookies (all 16). Sent on auth + set on response.
    - .rdp download flow (token issue, /rdp/download/<token>, build_rdp_content).
    - server_master CRUD.
    - users CRUD filters (active_only, client_name, server_id), enable/disable.
    - validate_user_payload, validate_server_payload, parse_body, USER_SELECT.
    - Error response shape: {status:"error", code:"...", message:"..."}.
    - verify_schema() at startup. Schema ownership is external (migrate_*.py).

SCHEMA OWNERSHIP
    This file does NOT create or alter tables. DDL lives in:
        migrate_v3.py   -- server_master, users
        migrate_v5.py   -- rdp_download_tokens
        migrate_v6.py   -- clients                          (NEW)

ENV
    MIRACLE_API_KEY  Required. Shared secret for /admin/* routes.
    MIRACLE_DB_PATH  Optional. Defaults to /etc/miracle-registry/miracle.db
"""

import logging
import os
import re
import secrets
import sqlite3
import sys
import time
from contextlib import contextmanager
from functools import wraps
from urllib.parse import quote

import requests as req
from flask import Flask, Response, jsonify, make_response, redirect, request

# ============================================================
#  CONFIG
# ============================================================

DB_PATH         = os.environ.get("MIRACLE_DB_PATH", "/etc/miracle-registry/miracle.db")
API_KEY         = os.environ.get("MIRACLE_API_KEY", "")
LOG_PATH        = "/var/log/miracle-router.log"
TSPLUS_TIMEOUT  = 10
REQUIRED_TABLES = ("server_master", "users", "rdp_download_tokens", "clients")

# RemoteApp .rdp file settings
TSPLUS_RDP_PORT = 59359           # same for all TSplus servers
RDP_TOKEN_TTL_SECONDS = 300       # 5 minutes

# uKey cookie -- long-lived, JS-readable so the login page can recover
# after logout / reload without keeping the user logged in.
UKEY_COOKIE_NAME    = "miracle_ukey"
UKEY_COOKIE_MAX_AGE = 30 * 24 * 3600   # 30 days

# Redirect targets per preference
REDIRECT_HTML5  = "/workspace"
REDIRECT_REMOTE = "/workspace-remote"

# Validation patterns
USERNAME_RE    = re.compile(r"^[A-Za-z0-9_]{1,64}$")
EMAIL_RE       = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
MOBILE_RE      = re.compile(r"^\+?[0-9]{7,15}$")
IPV4_RE        = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\. ]{1,64}$")
TOKEN_RE       = re.compile(r"^[a-f0-9]{32}$")
UKEY_RE        = re.compile(r"^[A-Za-z0-9]{8}$")
CLIENT_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

# TSplus browser fingerprint cookies. DO NOT change.
TSPLUS_REQUEST_COOKIES = {
    '_buttonLogOn'                                        : 'Log on',
    'Domain_Editbox3'                                     : '',
    'accesstypeuserchoice_accesstypeuserchoice_html5'     : 'true',
    'accesstypeuserchoice_accesstypeuserchoice_java'      : 'false',
    'accesstypeuserchoice_accesstypeuserchoice_remoteapp' : 'false',
    'accesstypeuserchoice_accesstypeuserchoice_remoteapp2': 'false',
    '_'                                                   : 'Download Plugin',
    '_sp-phonenumber'                                     : '',
    '_sp-register'                                        : 'Receive SMS',
    '_sp-emailaddress'                                    : '',
    '_sp-sendemail'                                       : 'Send e-mail',
    '_sp-verify'                                          : 'Validate',
    '_sp-full-username'                                   : '',
    '_reset-windows-password-choice-validate'             : 'Validate',
    'server'                                              : '-1',
}

# ============================================================
#  RDP TEMPLATE
# ============================================================
#
# The full address and username lines are substituted per request.
# Other settings come from the template Yash provided.
# Note: Windows .rdp files traditionally use CRLF line endings.

RDP_TEMPLATE = (
    "screen mode id:i:2\r\n"
    "use multimon:i:0\r\n"
    "desktopwidth:i:800\r\n"
    "desktopheight:i:600\r\n"
    "session bpp:i:32\r\n"
    "winposstr:s:0,3,0,0,800,600\r\n"
    "compression:i:1\r\n"
    "keyboardhook:i:2\r\n"
    "audiocapturemode:i:0\r\n"
    "videoplaybackmode:i:1\r\n"
    "connection type:i:7\r\n"
    "networkautodetect:i:1\r\n"
    "bandwidthautodetect:i:1\r\n"
    "displayconnectionbar:i:1\r\n"
    "enableworkspacereconnect:i:0\r\n"
    "disable wallpaper:i:0\r\n"
    "allow font smoothing:i:0\r\n"
    "allow desktop composition:i:0\r\n"
    "disable full window drag:i:1\r\n"
    "disable menu anims:i:1\r\n"
    "disable themes:i:0\r\n"
    "disable cursor setting:i:0\r\n"
    "bitmapcachepersistenable:i:1\r\n"
    "full address:s:{server_ip}:{rdp_port}\r\n"
    "username:s:{username}\r\n"
    "audiomode:i:0\r\n"
    "redirectprinters:i:0\r\n"
    "redirectcomports:i:0\r\n"
    "redirectsmartcards:i:1\r\n"
    "redirectwebauthn:i:1\r\n"
    "redirectclipboard:i:1\r\n"
    "redirectposdevices:i:0\r\n"
    "autoreconnection enabled:i:1\r\n"
    "authentication level:i:2\r\n"
    "prompt for credentials:i:0\r\n"
    "negotiate security layer:i:1\r\n"
    "remoteapplicationmode:i:0\r\n"
    "alternate shell:s:\r\n"
    "shell working directory:s:\r\n"
    "gatewayhostname:s:\r\n"
    "gatewayusagemethod:i:4\r\n"
    "gatewaycredentialssource:i:4\r\n"
    "gatewayprofileusagemethod:i:0\r\n"
    "promptcredentialonce:i:0\r\n"
    "gatewaybrokeringtype:i:0\r\n"
    "use redirection server name:i:0\r\n"
    "rdgiskdcproxy:i:0\r\n"
    "kdcproxyname:s:\r\n"
    "enablerdsaadauth:i:0\r\n"
)


def build_rdp_content(server_ip, username):
    """Substitute the per-user values into the template."""
    return RDP_TEMPLATE.format(
        server_ip=server_ip,
        rdp_port=TSPLUS_RDP_PORT,
        username=username,
    )


# ============================================================
#  LOGGING
# ============================================================

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("miracle-router")

# ============================================================
#  APP
# ============================================================

app = Flask(__name__)


# ============================================================
#  DATABASE
# ============================================================

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def verify_schema():
    """Confirm required tables exist. App does NOT create them."""
    if not os.path.exists(DB_PATH):
        msg = (
            "FATAL: Database file not found at {}\n"
            "       Run the migrations first:\n"
            "         sudo python3 migrate_v3.py    # base tables\n"
            "         sudo python3 migrate_v5.py    # rdp_download_tokens\n"
            "         sudo python3 migrate_v6.py    # clients (uKey)"
        ).format(DB_PATH)
        log.error(msg)
        sys.stderr.write(msg + "\n")
        sys.exit(1)

    try:
        with db() as conn:
            existing = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
    except sqlite3.Error as e:
        msg = "FATAL: Cannot open database at {}: {}".format(DB_PATH, e)
        log.error(msg)
        sys.stderr.write(msg + "\n")
        sys.exit(1)

    missing = [t for t in REQUIRED_TABLES if t not in existing]
    if missing:
        hints = []
        if "rdp_download_tokens" in missing:
            hints.append("  sudo python3 migrate_v5.py    # rdp_download_tokens")
        if "clients" in missing:
            hints.append("  sudo python3 migrate_v6.py    # clients (uKey)")
        if "server_master" in missing or "users" in missing:
            hints.append("  sudo python3 migrate_v3.py    # base tables")
        msg = (
            "FATAL: Required tables missing in {}: {}\n"
            "       Run:\n{}"
        ).format(DB_PATH, ", ".join(missing), "\n".join(hints) if hints else "  (see migrate_*.py)")
        log.error(msg)
        sys.stderr.write(msg + "\n")
        sys.exit(1)

    log.info("Schema check OK. Tables present: %s", sorted(existing))


# ============================================================
#  AUTH DECORATOR
# ============================================================

def require_api_key(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        provided = request.headers.get("X-API-Key", "")
        if not API_KEY:
            log.error("MIRACLE_API_KEY env var not set -- refusing all admin requests.")
            return jsonify({"error": "Server misconfigured: API key not set"}), 500
        if provided != API_KEY:
            return jsonify({"error": "Invalid or missing API key"}), 401
        return fn(*args, **kwargs)
    return wrapper


# ============================================================
#  VALIDATION
# ============================================================

def validate_user_payload(data, partial=False):
    errors = []
    cleaned = {}

    required = ("username", "client_name", "email", "mobile", "server_id")
    for f in required:
        if f in data and data[f] is not None and str(data[f]).strip() != "":
            cleaned[f] = data[f]
        elif not partial:
            errors.append("Missing required field: " + f)

    if "username" in cleaned:
        u = str(cleaned["username"]).strip()
        if not USERNAME_RE.match(u):
            errors.append("username must be 1-64 chars (A-Z, a-z, 0-9, _)")
        cleaned["username"] = u

    if "client_name" in cleaned:
        c = str(cleaned["client_name"]).strip()
        if not c or len(c) > 128:
            errors.append("client_name must be 1-128 chars")
        cleaned["client_name"] = c

    if "email" in cleaned:
        e = str(cleaned["email"]).strip().lower()
        if not EMAIL_RE.match(e) or len(e) > 254:
            errors.append("email format invalid")
        cleaned["email"] = e

    if "mobile" in cleaned:
        m = str(cleaned["mobile"]).strip().replace(" ", "").replace("-", "")
        if not MOBILE_RE.match(m):
            errors.append("mobile must be 7-15 digits, optional + prefix")
        cleaned["mobile"] = m

    if "server_id" in cleaned:
        try:
            cleaned["server_id"] = int(cleaned["server_id"])
        except (TypeError, ValueError):
            errors.append("server_id must be an integer")

    if "is_active" in data:
        v = data["is_active"]
        if isinstance(v, bool):
            cleaned["is_active"] = 1 if v else 0
        elif v in (0, 1, "0", "1"):
            cleaned["is_active"] = int(v)
        else:
            errors.append("is_active must be 0 or 1")

    return errors, cleaned


def validate_server_payload(data, partial=False):
    errors = []
    cleaned = {}

    if "server_name" in data and data["server_name"] is not None and str(data["server_name"]).strip() != "":
        n = str(data["server_name"]).strip()
        if not SERVER_NAME_RE.match(n):
            errors.append("server_name must be 1-64 chars (letters, digits, _ - . space)")
        cleaned["server_name"] = n
    elif not partial:
        errors.append("Missing required field: server_name")

    if "server_ip" in data and data["server_ip"] is not None and str(data["server_ip"]).strip() != "":
        ip = str(data["server_ip"]).strip()
        if not IPV4_RE.match(ip):
            errors.append("server_ip must be a valid IPv4 address")
        else:
            try:
                if any(not 0 <= int(p) <= 255 for p in ip.split(".")):
                    errors.append("server_ip octets must be 0-255")
            except ValueError:
                errors.append("server_ip octets must be numeric")
        cleaned["server_ip"] = ip
    elif not partial:
        errors.append("Missing required field: server_ip")

    return errors, cleaned


def parse_body():
    """Accept JSON or form-encoded bodies uniformly."""
    j = request.get_json(silent=True)
    if j and isinstance(j, dict):
        return j
    return request.form.to_dict()


# ============================================================
#  PUBLIC ROUTES
# ============================================================

@app.route("/health", methods=["GET"])
def health():
    try:
        with db() as conn:
            users    = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            active   = conn.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
            srvs     = conn.execute("SELECT COUNT(*) FROM server_master").fetchone()[0]
            clients_count = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
            tokens   = conn.execute(
                "SELECT COUNT(*) FROM rdp_download_tokens WHERE used_at IS NULL"
            ).fetchone()[0]
        return jsonify({
            "status":        "ok",
            "users":         users,
            "active":        active,
            "servers":       srvs,
            "total_clients": clients_count,
            "pending_rdp_tokens": tokens,
        })
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


def _cleanup_old_rdp_tokens(conn):
    """Lazy cleanup: delete rdp_download_tokens older than the TTL."""
    conn.execute(
        "DELETE FROM rdp_download_tokens "
        "WHERE created_at < datetime('now', '-' || ? || ' seconds')",
        (RDP_TOKEN_TTL_SECONDS,),
    )


@app.route("/login", methods=["POST"])
def login():
    """
    Login flow.
      - Reads username, password, preference (html5|remote), ukey.
      - Validates field formats. uKey is REQUIRED (v3.5).
      - SQLite strict bind: users JOIN clients ON client_name (NOCASE)
        WHERE username = ? AND clients.ukey = ?
      - User must be is_active=1.
      - Calls TSplus action=cp to verify password against Windows.
      - On success with preference=html5: returns {redirect:"/workspace"}.
      - On success with preference=remote: generates a one-time download
        token for the .rdp file, returns {redirect:"/workspace-remote",
        rdp_url:"/rdp/download/<token>"}.
      - Sets miracle_ukey cookie (30 days, JS-readable) so the login
        page can recover the uKey after logout / reload.
      - All 16 TSplus fingerprint cookies are set on the response as
        before; miracle_ukey is purely additive.

    The .rdp download is gated by the token (single-use, 5 min TTL).
    Password is NOT stored anywhere -- mstsc will prompt the user.
    """
    if request.content_type and 'application/json' in request.content_type:
        data       = request.get_json(silent=True) or {}
        username   = data.get('username',   '').strip()
        password   = data.get('password',   '').strip()
        preference = data.get('preference', 'html5').strip().lower()
        ukey       = data.get('ukey',       '').strip()
    else:
        username   = request.form.get('username',   '').strip()
        password   = request.form.get('password',   '').strip()
        preference = request.form.get('preference', 'html5').strip().lower()
        ukey       = request.form.get('ukey',       '').strip()

    if preference not in ('html5', 'remote'):
        preference = 'html5'

    if not username:
        return jsonify({"status": "error", "code": "MISSING_FIELDS",
                        "message": "Username required"}), 400
    if not USERNAME_RE.match(username):
        return jsonify({"status": "error", "code": "INVALID_USERNAME",
                        "message": "Invalid username format"}), 400
    if not password:
        return jsonify({"status": "error", "code": "MISSING_FIELDS",
                        "message": "Password required"}), 400
    if not ukey:
        return jsonify({"status": "error", "code": "MISSING_UKEY",
                        "message": "Access link required."}), 400
    if not UKEY_RE.match(ukey):
        log.warning("Login: bad ukey format for '%s' from %s",
                    username, request.remote_addr)
        return jsonify({"status": "error", "code": "INVALID_UKEY",
                        "message": "Invalid access link."}), 400

    # 1. SQLite strict bind: username + ukey must point to the same row
    #    via users.client_name == clients.client_name.
    with db() as conn:
        row = conn.execute("""
            SELECT u.id, u.username, u.is_active,
                   u.client_name, c.ukey AS bound_ukey,
                   s.server_ip
            FROM   users u
            JOIN   clients       c ON c.client_name = u.client_name COLLATE NOCASE
            JOIN   server_master s ON s.id = u.server_id
            WHERE  u.username = ?
              AND  c.ukey     = ? COLLATE NOCASE
        """, (username, ukey)).fetchone()

    if not row:
        # Could be: unknown user, wrong uKey for this user, or user's
        # client has no clients-table row yet. Same surface error -- do
        # not leak which.
        log.warning("Login bind MISS: user='%s' ukey=%s from %s",
                    username, ukey, request.remote_addr)
        return jsonify({"status": "error", "code": "INVALID_CREDENTIALS",
                        "message": "Invalid access link or credentials."}), 401

    if row["is_active"] != 1:
        log.warning("Login rejected: disabled account '%s'", username)
        return jsonify({"status": "error", "code": "ACCOUNT_DISABLED",
                        "message": "Account disabled. Contact your administrator."}), 403

    ip = row["server_ip"]

    # 2. TSplus credential check (same for both preferences)
    cookies = dict(TSPLUS_REQUEST_COOKIES)
    cookies['username_Editbox1'] = username

    try:
        base_url  = 'http://{}'.format(ip)
        timestamp = str(int(time.time() * 1000))
        payload   = 'action=cp&l={}&p={}&d=&f=&t={}'.format(
            username.lower(),
            quote(password, safe=''),
            timestamp
        )
        tsplus_resp = req.post(
            '{}/cgi-bin/hb.exe'.format(base_url),
            data=payload,
            headers={
                'Content-Type'   : 'text/plain;charset=UTF-8',
                'Host'           : ip,
                'Origin'         : base_url,
                'Referer'        : '{}/'.format(base_url),
                'User-Agent'     : 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:149.0) Gecko/20100101 Firefox/149.0',
                'Accept'         : '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Connection'     : 'keep-alive',
            },
            cookies=cookies,
            timeout=TSPLUS_TIMEOUT,
        )

        log.info("TSplus response for '%s' (pref=%s): status=%s body=%s",
                 username, preference, tsplus_resp.status_code, tsplus_resp.text[:200])

    except req.exceptions.ConnectionError:
        log.error("TSplus unreachable: %s", ip)
        return jsonify({"status": "error", "code": "BACKEND_UNAVAILABLE",
                        "message": "Authentication server unreachable"}), 503
    except req.exceptions.Timeout:
        log.error("TSplus timeout: %s", ip)
        return jsonify({"status": "error", "code": "BACKEND_UNAVAILABLE",
                        "message": "Authentication server timed out"}), 503
    except Exception as e:
        log.error("TSplus auth exception for '%s': %s", username, str(e))
        return jsonify({"status": "error", "code": "INVALID_CREDENTIALS",
                        "message": "Authentication failed"}), 500

    try:
        tsplus_json   = tsplus_resp.json()
        tsplus_status = tsplus_json.get('Status', '').lower()
    except Exception:
        tsplus_status = ''

    if tsplus_status != 'ok':
        log.warning("TSplus rejected credentials for '%s' -> %s (body: %s)",
                    username, ip, tsplus_resp.text[:100])
        return jsonify({"status": "error", "code": "INVALID_CREDENTIALS",
                        "message": "Invalid username or password"}), 401

    log.info("Login OK: '%s' -> %s (pref=%s ukey=%s client=%s)",
             username, ip, preference, ukey, row["client_name"])

    # 3. Build response based on preference
    response_payload = {
        "status":     "ok",
        "preference": preference,
    }

    if preference == 'remote':
        # Generate a single-use download token for the .rdp file
        token = secrets.token_hex(16)
        try:
            with db() as conn:
                _cleanup_old_rdp_tokens(conn)
                conn.execute(
                    "INSERT INTO rdp_download_tokens "
                    "(token, username, server_ip, created_at) "
                    "VALUES (?, ?, ?, datetime('now'))",
                    (token, username, ip),
                )
            log.info("RDP token issued: user=%s server=%s", username, ip)
        except sqlite3.Error as e:
            log.error("Failed to issue RDP token for '%s': %s", username, e)
            return jsonify({"status": "error", "code": "TOKEN_ISSUE_FAILED",
                            "message": "Could not prepare download. Please try again."}), 500

        response_payload["redirect"] = REDIRECT_REMOTE
        response_payload["rdp_url"]  = "/rdp/download/{}".format(token)
    else:
        response_payload["redirect"] = REDIRECT_HTML5

    resp = make_response(jsonify(response_payload))

    # Routing cookie + all 16 TSplus fingerprint cookies (kept for HTML5
    # flow and for any catch-all proxy needs).
    resp.set_cookie('miracle_target', ip, path='/', samesite='Strict', httponly=True)
    for name, value in cookies.items():
        resp.set_cookie(name, value, path='/', samesite='Lax')

    # uKey cookie -- long-lived, JS-readable, additive. Used by the
    # login page to recover the uKey after logout or page reload.
    resp.set_cookie(
        UKEY_COOKIE_NAME, ukey,
        path='/',
        max_age=UKEY_COOKIE_MAX_AGE,
        samesite='Lax',
        httponly=False,
    )

    return resp


@app.route("/logout", methods=["GET", "POST"])
def logout():
    """Clear session cookies. Preserve miracle_ukey and redirect to
    /?uKey=<value> so the user can immediately sign in again."""
    preserved_ukey = (request.cookies.get(UKEY_COOKIE_NAME) or "").strip()
    if UKEY_RE.match(preserved_ukey):
        target = "/?uKey={}".format(preserved_ukey)
    else:
        preserved_ukey = ""
        target = "/"

    resp = make_response(redirect(target))

    to_clear = list(TSPLUS_REQUEST_COOKIES.keys()) + ['miracle_target', 'username_Editbox1']
    for name in to_clear:
        resp.set_cookie(name, '', expires=0, path='/')

    # Re-set the uKey cookie (preserve it across logout). If the user
    # had no valid uKey cookie, leave it absent.
    if preserved_ukey:
        resp.set_cookie(
            UKEY_COOKIE_NAME, preserved_ukey,
            path='/',
            max_age=UKEY_COOKIE_MAX_AGE,
            samesite='Lax',
            httponly=False,
        )

    log.info("Logout from %s (ukey preserved: %s)",
             request.remote_addr, preserved_ukey or "(none)")
    return resp


# ============================================================
#  RDP DOWNLOAD
# ============================================================

@app.route("/rdp/download/<token>", methods=["GET"])
def rdp_download(token):
    """
    Single-use download of a per-user .rdp file.

      - Token must be 32 lowercase hex chars
      - Must exist in rdp_download_tokens
      - Must not have been used (used_at IS NULL)
      - Must be younger than RDP_TOKEN_TTL_SECONDS
      - Marks the token used atomically with the read

    Returns the .rdp content as application/x-rdp with a download
    Content-Disposition. mstsc.exe will then prompt the user for password.
    """
    if not TOKEN_RE.match(token or ''):
        log.warning("rdp_download: bad token format from %s: %r",
                    request.remote_addr, (token or '')[:80])
        return Response("Bad request", status=400)

    with db() as conn:
        # Atomic test-and-set: only consume if not previously used and
        # not expired. We rely on SQLite's serialised transactions.
        row = conn.execute("""
            SELECT username, server_ip, used_at, created_at,
                   (strftime('%s','now') - strftime('%s', created_at)) AS age_seconds
            FROM   rdp_download_tokens
            WHERE  token = ?
        """, (token,)).fetchone()

        if not row:
            log.warning("rdp_download: unknown token from %s", request.remote_addr)
            return Response("Not found", status=404)

        if row["used_at"] is not None:
            log.warning("rdp_download: token already used (user=%s, used_at=%s) from %s",
                        row["username"], row["used_at"], request.remote_addr)
            return Response("Gone: token already used", status=410)

        if row["age_seconds"] is not None and row["age_seconds"] > RDP_TOKEN_TTL_SECONDS:
            log.warning("rdp_download: token expired (user=%s, age=%ss) from %s",
                        row["username"], row["age_seconds"], request.remote_addr)
            return Response("Gone: token expired", status=410)

        # Mark as used
        conn.execute(
            "UPDATE rdp_download_tokens SET used_at = datetime('now') WHERE token = ?",
            (token,),
        )

    # Build the .rdp file content
    rdp_content = build_rdp_content(row["server_ip"], row["username"])

    log.info("RDP downloaded: user=%s server=%s from %s",
             row["username"], row["server_ip"], request.remote_addr)

    response = make_response(rdp_content)
    response.headers["Content-Type"]        = "application/x-rdp"
    response.headers["Content-Disposition"] = 'attachment; filename="miracle.rdp"'
    response.headers["Cache-Control"]       = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"]              = "no-cache"
    return response


# ============================================================
#  ADMIN: server_master CRUD
# ============================================================

@app.route("/admin/servers", methods=["POST"])
@require_api_key
def server_create():
    data = parse_body()
    errors, cleaned = validate_server_payload(data, partial=False)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    try:
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO server_master (server_name, server_ip) VALUES (?, ?)",
                (cleaned["server_name"], cleaned["server_ip"]),
            )
            new_id = cur.lastrowid
            row = conn.execute(
                "SELECT * FROM server_master WHERE id = ?", (new_id,)
            ).fetchone()
    except sqlite3.IntegrityError as e:
        msg = str(e).lower()
        if "server_name" in msg:
            return jsonify({"error": "server_name already exists"}), 409
        if "server_ip" in msg:
            return jsonify({"error": "server_ip already exists"}), 409
        return jsonify({"error": "Conflict", "detail": str(e)}), 409

    log.info("Server created: id=%s name=%s ip=%s",
             new_id, cleaned['server_name'], cleaned['server_ip'])
    return jsonify(dict(row)), 201


@app.route("/admin/servers", methods=["GET"])
@require_api_key
def server_list():
    with db() as conn:
        rows = conn.execute("""
            SELECT s.*,
                   (SELECT COUNT(*) FROM users u WHERE u.server_id = s.id) AS user_count
            FROM   server_master s
            ORDER  BY s.id
        """).fetchall()
    return jsonify({"servers": [dict(r) for r in rows], "count": len(rows)})


@app.route("/admin/servers/<int:server_id>", methods=["GET"])
@require_api_key
def server_get(server_id):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM server_master WHERE id = ?", (server_id,)
        ).fetchone()
    if not row:
        return jsonify({"error": "Server not found"}), 404
    return jsonify(dict(row))


@app.route("/admin/servers/<int:server_id>", methods=["PUT"])
@require_api_key
def server_update(server_id):
    data = parse_body()
    errors, cleaned = validate_server_payload(data, partial=True)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    if not cleaned:
        return jsonify({"error": "No fields to update"}), 400

    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM server_master WHERE id = ?", (server_id,)
        ).fetchone()
        if not existing:
            return jsonify({"error": "Server not found"}), 404

        sets   = ", ".join("{} = ?".format(k) for k in cleaned.keys())
        params = list(cleaned.values()) + [server_id]

        try:
            conn.execute(
                "UPDATE server_master SET {}, updated_at = datetime('now') WHERE id = ?".format(sets),
                params,
            )
        except sqlite3.IntegrityError as e:
            msg = str(e).lower()
            if "server_name" in msg:
                return jsonify({"error": "server_name already exists"}), 409
            if "server_ip" in msg:
                return jsonify({"error": "server_ip already exists"}), 409
            return jsonify({"error": "Conflict", "detail": str(e)}), 409

        row = conn.execute(
            "SELECT * FROM server_master WHERE id = ?", (server_id,)
        ).fetchone()

    log.info("Server updated: id=%s fields=%s", server_id, list(cleaned.keys()))
    return jsonify(dict(row))


@app.route("/admin/servers/<int:server_id>", methods=["DELETE"])
@require_api_key
def server_delete(server_id):
    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM server_master WHERE id = ?", (server_id,)
        ).fetchone()
        if not existing:
            return jsonify({"error": "Server not found"}), 404

        user_count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE server_id = ?", (server_id,)
        ).fetchone()[0]
        if user_count > 0:
            return jsonify({
                "error":      "Cannot delete server while users reference it",
                "user_count": user_count,
                "hint":       "Delete or reassign those users first",
            }), 409

        conn.execute("DELETE FROM server_master WHERE id = ?", (server_id,))

    log.info("Server deleted: id=%s", server_id)
    return jsonify({"deleted": True, "id": server_id})


# ============================================================
#  ADMIN: clients CRUD       (NEW in v3.5)
# ============================================================

@app.route("/admin/clients", methods=["POST"])
@require_api_key
def client_create():
    """Create a (client_name, ukey) row. PowerShell Setup calls this
    BEFORE creating any user."""
    data = parse_body()

    client_name = str(data.get("client_name", "") or "").strip()
    ukey        = str(data.get("ukey",        "") or "").strip()

    if not client_name:
        return jsonify({"error": "Missing required field: client_name"}), 400
    if not CLIENT_NAME_RE.match(client_name):
        return jsonify({"error": "client_name must be 1-64 chars [A-Za-z0-9_-]"}), 400
    if not ukey:
        return jsonify({"error": "Missing required field: ukey"}), 400
    if not UKEY_RE.match(ukey):
        return jsonify({"error": "ukey must be 8 alphanumeric chars"}), 400

    try:
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO clients (client_name, ukey) VALUES (?, ?)",
                (client_name, ukey),
            )
            new_id = cur.lastrowid
            row = conn.execute(
                "SELECT * FROM clients WHERE id = ?", (new_id,)
            ).fetchone()
    except sqlite3.IntegrityError as e:
        msg = str(e).lower()
        # Surface which field clashed
        with db() as conn:
            by_name = conn.execute(
                "SELECT id, client_name, ukey FROM clients WHERE client_name = ? COLLATE NOCASE",
                (client_name,),
            ).fetchone()
            by_ukey = conn.execute(
                "SELECT id, client_name, ukey FROM clients WHERE ukey = ? COLLATE NOCASE",
                (ukey,),
            ).fetchone()
        if by_name:
            return jsonify({
                "error":                "client_name already exists",
                "existing_id":          by_name["id"],
                "existing_client_name": by_name["client_name"],
                "existing_ukey":        by_name["ukey"],
            }), 409
        if by_ukey:
            return jsonify({
                "error":                "ukey already in use",
                "existing_id":          by_ukey["id"],
                "existing_client_name": by_ukey["client_name"],
                "existing_ukey":        by_ukey["ukey"],
            }), 409
        return jsonify({"error": "Conflict", "detail": str(e)}), 409

    log.info("Client created: id=%s name=%s ukey=%s", new_id, client_name, ukey)
    return jsonify(dict(row)), 201


@app.route("/admin/clients", methods=["GET"])
@require_api_key
def client_list():
    with db() as conn:
        rows = conn.execute("""
            SELECT c.*,
                   (SELECT COUNT(*) FROM users u
                      WHERE u.client_name = c.client_name COLLATE NOCASE) AS user_count
            FROM   clients c
            ORDER  BY c.client_name
        """).fetchall()
    return jsonify({"clients": [dict(r) for r in rows], "count": len(rows)})


@app.route("/admin/clients/<int:client_id>", methods=["GET"])
@require_api_key
def client_get(client_id):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM clients WHERE id = ?", (client_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Client not found"}), 404
        users = conn.execute(
            "SELECT id, username, is_active FROM users "
            "WHERE client_name = ? COLLATE NOCASE ORDER BY username",
            (row["client_name"],),
        ).fetchall()
    out = dict(row)
    out["users"] = [dict(u) for u in users]
    out["user_count"] = len(users)
    return jsonify(out)


@app.route("/admin/clients/by-name/<client_name>", methods=["GET"])
@require_api_key
def client_by_name(client_name):
    if not CLIENT_NAME_RE.match(client_name or ""):
        return jsonify({"error": "invalid client_name"}), 400
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM clients WHERE client_name = ? COLLATE NOCASE",
            (client_name,),
        ).fetchone()
        if not row:
            return jsonify({"error": "Client not found"}), 404
        users = conn.execute(
            "SELECT id, username, is_active FROM users "
            "WHERE client_name = ? COLLATE NOCASE ORDER BY username",
            (row["client_name"],),
        ).fetchall()
    out = dict(row)
    out["users"] = [dict(u) for u in users]
    out["user_count"] = len(users)
    return jsonify(out)


@app.route("/admin/clients/exists/<client_name>", methods=["GET"])
@require_api_key
def client_exists(client_name):
    """Duplicate-check endpoint. ALWAYS returns 200. Used by
    MiracleCloud-Setup.ps1 pre-flight."""
    if not CLIENT_NAME_RE.match(client_name or ""):
        return jsonify({"exists": False, "error": "invalid client_name"}), 200

    with db() as conn:
        row = conn.execute(
            "SELECT id, client_name, ukey FROM clients WHERE client_name = ? COLLATE NOCASE",
            (client_name,),
        ).fetchone()
        if not row:
            return jsonify({"exists": False})

        user_count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE client_name = ? COLLATE NOCASE",
            (row["client_name"],),
        ).fetchone()[0]

        # Most-common server for this client (informational)
        srv = conn.execute("""
            SELECT s.server_ip, s.server_name, COUNT(u.id) AS n
            FROM   users u
            JOIN   server_master s ON s.id = u.server_id
            WHERE  u.client_name = ? COLLATE NOCASE
            GROUP  BY u.server_id
            ORDER  BY n DESC
            LIMIT  1
        """, (row["client_name"],)).fetchone()

    out = {
        "exists":      True,
        "id":          row["id"],
        "client_name": row["client_name"],
        "ukey":        row["ukey"],
        "user_count":  user_count,
    }
    if srv:
        out["server_ip"]   = srv["server_ip"]
        out["server_name"] = srv["server_name"]
    return jsonify(out)


@app.route("/admin/clients/<int:client_id>", methods=["PUT"])
@require_api_key
def client_update(client_id):
    data = parse_body()

    cleaned = {}
    if "client_name" in data:
        v = str(data["client_name"] or "").strip()
        if not v:
            return jsonify({"error": "client_name cannot be empty"}), 400
        if not CLIENT_NAME_RE.match(v):
            return jsonify({"error": "client_name must be 1-64 chars [A-Za-z0-9_-]"}), 400
        cleaned["client_name"] = v
    if "ukey" in data:
        v = str(data["ukey"] or "").strip()
        if not UKEY_RE.match(v):
            return jsonify({"error": "ukey must be 8 alphanumeric chars"}), 400
        cleaned["ukey"] = v

    if not cleaned:
        return jsonify({"error": "No fields to update"}), 400

    with db() as conn:
        existing = conn.execute(
            "SELECT * FROM clients WHERE id = ?", (client_id,)
        ).fetchone()
        if not existing:
            return jsonify({"error": "Client not found"}), 404

        try:
            # Cascade the new client_name into users (denormalized FK)
            if "client_name" in cleaned and cleaned["client_name"].lower() != existing["client_name"].lower():
                conn.execute(
                    "UPDATE users SET client_name = ? WHERE client_name = ? COLLATE NOCASE",
                    (cleaned["client_name"], existing["client_name"]),
                )

            sets   = ", ".join("{} = ?".format(k) for k in cleaned.keys())
            params = list(cleaned.values()) + [client_id]
            conn.execute(
                "UPDATE clients SET {} WHERE id = ?".format(sets),
                params,
            )
        except sqlite3.IntegrityError as e:
            msg = str(e).lower()
            if "client_name" in msg:
                return jsonify({"error": "client_name already exists"}), 409
            if "ukey" in msg:
                return jsonify({"error": "ukey already in use"}), 409
            return jsonify({"error": "Conflict", "detail": str(e)}), 409

        row = conn.execute(
            "SELECT * FROM clients WHERE id = ?", (client_id,)
        ).fetchone()

    log.info("Client updated: id=%s fields=%s", client_id, list(cleaned.keys()))
    return jsonify(dict(row))


@app.route("/admin/clients/<int:client_id>", methods=["DELETE"])
@require_api_key
def client_delete(client_id):
    """Delete the clients row only. Does NOT touch users.
    Use DELETE /admin/users/by-client/<name> for full cascade."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM clients WHERE id = ?", (client_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Client not found"}), 404
        conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))

    log.info("Client deleted (row only): id=%s name=%s",
             client_id, row["client_name"])
    return jsonify({
        "deleted":     True,
        "id":          client_id,
        "client_name": row["client_name"],
        "ukey":        row["ukey"],
    })


# ============================================================
#  ADMIN: users CRUD
# ============================================================

USER_SELECT = """
    SELECT u.id, u.username, u.client_name, u.email, u.mobile,
           u.server_id, u.is_active, u.created_at, u.updated_at,
           s.server_name, s.server_ip
    FROM   users u
    JOIN   server_master s ON s.id = u.server_id
"""


@app.route("/admin/users", methods=["POST"])
@require_api_key
def user_create():
    data = parse_body()
    errors, cleaned = validate_user_payload(data, partial=False)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    with db() as conn:
        srv = conn.execute(
            "SELECT id FROM server_master WHERE id = ?", (cleaned["server_id"],)
        ).fetchone()
        if not srv:
            return jsonify({"error": "server_id {} does not exist".format(cleaned['server_id'])}), 400

        # client_name MUST exist in clients table (uKey precondition)
        client_row = conn.execute(
            "SELECT id FROM clients WHERE client_name = ? COLLATE NOCASE",
            (cleaned["client_name"],),
        ).fetchone()
        if not client_row:
            return jsonify({
                "error": "UNKNOWN_CLIENT",
                "hint":  "Create the client first via POST /admin/clients "
                         "with client_name='{}'".format(cleaned["client_name"]),
            }), 400

        try:
            cur = conn.execute("""
                INSERT INTO users (username, client_name, email, mobile, server_id, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                cleaned["username"],
                cleaned["client_name"],
                cleaned["email"],
                cleaned["mobile"],
                cleaned["server_id"],
                cleaned.get("is_active", 1),
            ))
            new_id = cur.lastrowid
        except sqlite3.IntegrityError as e:
            return jsonify({"error": "Username already exists", "detail": str(e)}), 409

        row = conn.execute(USER_SELECT + " WHERE u.id = ?", (new_id,)).fetchone()

    log.info("User created: id=%s username=%s", new_id, cleaned['username'])
    return jsonify(dict(row)), 201


@app.route("/admin/users", methods=["GET"])
@require_api_key
def user_list():
    active_only = request.args.get("active_only", "").lower() in ("1", "true", "yes")
    client      = request.args.get("client_name")
    server_id   = request.args.get("server_id")

    query  = USER_SELECT
    where  = []
    params = []

    if active_only:
        where.append("u.is_active = 1")
    if client:
        where.append("u.client_name = ?")
        params.append(client)
    if server_id:
        where.append("u.server_id = ?")
        params.append(server_id)

    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY u.id"

    with db() as conn:
        rows = conn.execute(query, params).fetchall()

    return jsonify({"users": [dict(r) for r in rows], "count": len(rows)})


@app.route("/admin/users/<int:user_id>", methods=["GET"])
@require_api_key
def user_get(user_id):
    with db() as conn:
        row = conn.execute(USER_SELECT + " WHERE u.id = ?", (user_id,)).fetchone()
    if not row:
        return jsonify({"error": "User not found"}), 404
    return jsonify(dict(row))


@app.route("/admin/users/<int:user_id>", methods=["PUT"])
@require_api_key
def user_update(user_id):
    data = parse_body()
    errors, cleaned = validate_user_payload(data, partial=True)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    if not cleaned:
        return jsonify({"error": "No fields to update"}), 400

    with db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            return jsonify({"error": "User not found"}), 404

        if "server_id" in cleaned:
            srv = conn.execute(
                "SELECT id FROM server_master WHERE id = ?", (cleaned["server_id"],)
            ).fetchone()
            if not srv:
                return jsonify({"error": "server_id {} does not exist".format(cleaned['server_id'])}), 400

        if "client_name" in cleaned:
            client_row = conn.execute(
                "SELECT id FROM clients WHERE client_name = ? COLLATE NOCASE",
                (cleaned["client_name"],),
            ).fetchone()
            if not client_row:
                return jsonify({
                    "error": "UNKNOWN_CLIENT",
                    "hint":  "Create the client first via POST /admin/clients "
                             "with client_name='{}'".format(cleaned["client_name"]),
                }), 400

        sets   = ", ".join("{} = ?".format(k) for k in cleaned.keys())
        params = list(cleaned.values()) + [user_id]

        try:
            conn.execute(
                "UPDATE users SET {}, updated_at = datetime('now') WHERE id = ?".format(sets),
                params,
            )
        except sqlite3.IntegrityError as e:
            return jsonify({"error": "Conflict", "detail": str(e)}), 409

        row = conn.execute(USER_SELECT + " WHERE u.id = ?", (user_id,)).fetchone()

    log.info("User updated: id=%s fields=%s", user_id, list(cleaned.keys()))
    return jsonify(dict(row))


@app.route("/admin/users/<int:user_id>", methods=["DELETE"])
@require_api_key
def user_delete(user_id):
    with db() as conn:
        existing = conn.execute(
            "SELECT username FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not existing:
            return jsonify({"error": "User not found"}), 404
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    log.info("User deleted: id=%s username=%s", user_id, existing['username'])
    return jsonify({"deleted": True, "id": user_id})


@app.route("/admin/users/<int:user_id>/disable", methods=["POST"])
@require_api_key
def user_disable(user_id):
    with db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            return jsonify({"error": "User not found"}), 404
        conn.execute(
            "UPDATE users SET is_active = 0, updated_at = datetime('now') WHERE id = ?",
            (user_id,),
        )
        row = conn.execute(USER_SELECT + " WHERE u.id = ?", (user_id,)).fetchone()
    log.info("User disabled: id=%s", user_id)
    return jsonify(dict(row))


@app.route("/admin/users/<int:user_id>/enable", methods=["POST"])
@require_api_key
def user_enable(user_id):
    with db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            return jsonify({"error": "User not found"}), 404
        conn.execute(
            "UPDATE users SET is_active = 1, updated_at = datetime('now') WHERE id = ?",
            (user_id,),
        )
        row = conn.execute(USER_SELECT + " WHERE u.id = ?", (user_id,)).fetchone()
    log.info("User enabled: id=%s", user_id)
    return jsonify(dict(row))


@app.route("/admin/users/by-client/<client_name>", methods=["DELETE"])
@require_api_key
def user_delete_by_client(client_name):
    """Cascade: delete all users for this client, AND remove the
    matching clients row (which holds the uKey). Single-call cleanup
    for MiracleCloud-Delete.ps1.

    Returns 200 even when nothing existed (v3.4 behavior preserved),
    with both `deleted` and `client_deleted` set to 0 in that case."""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, username FROM users WHERE client_name = ?", (client_name,)
        ).fetchall()

        client_row = conn.execute(
            "SELECT id, client_name, ukey FROM clients WHERE client_name = ? COLLATE NOCASE",
            (client_name,),
        ).fetchone()

        if rows:
            conn.execute("DELETE FROM users WHERE client_name = ?", (client_name,))

        client_deleted = 0
        ukey           = None
        canonical_name = client_name
        if client_row:
            conn.execute("DELETE FROM clients WHERE id = ?", (client_row["id"],))
            client_deleted = 1
            ukey           = client_row["ukey"]
            canonical_name = client_row["client_name"]

    deleted = [r["username"] for r in rows]
    log.info("Users+client deleted by client: %s users=%d client_row=%d ukey=%s",
             canonical_name, len(deleted), client_deleted, ukey)
    return jsonify({
        "deleted":        len(deleted),
        "usernames":      deleted,
        "client_deleted": client_deleted,
        "ukey":           ukey,
        "client_name":    canonical_name,
    })


@app.route("/admin/stats", methods=["GET"])
@require_api_key
def admin_stats():
    with db() as conn:
        total    = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active   = conn.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
        srvs     = conn.execute("SELECT COUNT(*) FROM server_master").fetchone()[0]
        clients_count = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        clients_dist  = conn.execute("SELECT COUNT(DISTINCT client_name) FROM users").fetchone()[0]
        pending  = conn.execute(
            "SELECT COUNT(*) FROM rdp_download_tokens WHERE used_at IS NULL"
        ).fetchone()[0]
        consumed = conn.execute(
            "SELECT COUNT(*) FROM rdp_download_tokens WHERE used_at IS NOT NULL"
        ).fetchone()[0]
        per_srv  = conn.execute("""
            SELECT s.id, s.server_name, s.server_ip,
                   COUNT(u.id) AS user_count
            FROM   server_master s
            LEFT   JOIN users u ON u.server_id = s.id
            GROUP  BY s.id
            ORDER  BY s.id
        """).fetchall()
    return jsonify({
        "total_users":              total,
        "active_users":             active,
        "disabled_users":           total - active,
        "total_servers":            srvs,
        "total_clients":            clients_count,
        "distinct_clients":         clients_dist,
        "rdp_tokens_pending":       pending,
        "rdp_tokens_consumed":      consumed,
        "per_server":               [dict(r) for r in per_srv],
    })


# ============================================================
#  STARTUP
# ============================================================

verify_schema()

if __name__ == "__main__":
    if not API_KEY:
        print("WARNING: MIRACLE_API_KEY env var not set. /admin/* will refuse all requests.")
    app.run(host="127.0.0.1", port=5001, debug=False)