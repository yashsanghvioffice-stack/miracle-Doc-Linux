"""
Public endpoints (no API key required):
    GET  /health   -- liveness probe + counts snapshot
    POST /login    -- the main credential flow (uKey bind + TSplus auth)
    *    /logout   -- clear session cookies, preserve miracle_ukey
"""

import sqlite3

from flask import Blueprint, jsonify, make_response, redirect, request

import messages as M
from config import (
    USERNAME_RE, UKEY_RE,
    UKEY_COOKIE_NAME, UKEY_COOKIE_MAX_AGE,
    REDIRECT_HTML5, REDIRECT_REMOTE,
    TSPLUS_REQUEST_COOKIES,
)
from logger import log

from dal.connection import db
from dal import stats_dal
from bl import auth_bl, tsplus_bl, rdp_bl
from bl.auth_bl import BindOutcome
from bl.tsplus_bl import TSplusUnreachable, TSplusTimeout, TSplusError


bp = Blueprint("public", __name__)


# ─── /health ──────────────────────────────────────────────────────

@bp.route("/health", methods=["GET"])
def health():
    try:
        with db() as conn:
            snap = stats_dal.health_snapshot(conn)
        payload = {"status": "ok"}
        payload.update(snap)
        return jsonify(payload)
    except Exception as e:
        # /health is for ops monitoring -- still returns the v3.4 contract.
        return jsonify({"status":  "error",
                        "code":    "HEALTH_CHECK_FAILED",
                        "message": "Health check failed",
                        "detail":  str(e)}), 500


# ─── /login ───────────────────────────────────────────────────────

@bp.route("/login", methods=["POST"])
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
        return jsonify({"status": "error", "code": M.CODE_MISSING_FIELDS,
                        "message": M.MSG_USERNAME_REQUIRED}), 400
    if not USERNAME_RE.match(username):
        return jsonify({"status": "error", "code": M.CODE_INVALID_USERNAME,
                        "message": M.MSG_INVALID_USERNAME}), 400
    if not password:
        return jsonify({"status": "error", "code": M.CODE_MISSING_FIELDS,
                        "message": M.MSG_PASSWORD_REQUIRED}), 400
    if not ukey:
        return jsonify({"status": "error", "code": M.CODE_MISSING_UKEY,
                        "message": M.MSG_MISSING_UKEY}), 400
    if not UKEY_RE.match(ukey):
        log.warning("Login: bad ukey format for '%s' from %s",
                    username, request.remote_addr)
        return jsonify({"status": "error", "code": M.CODE_INVALID_UKEY,
                        "message": M.MSG_INVALID_UKEY}), 400

    # 1. SQLite strict bind + is_active check (BL: auth_bl)
    with db() as conn:
        bind, row = auth_bl.find_authenticated_user(conn, username, ukey)

    if bind == BindOutcome.MISS:
        log.warning("Login bind MISS: user='%s' ukey=%s from %s",
                    username, ukey, request.remote_addr)
        return jsonify({"status": "error", "code": M.CODE_INVALID_CREDENTIALS,
                        "message": M.MSG_INVALID_CREDENTIALS}), 401

    if bind == BindOutcome.DISABLED:
        log.warning("Login rejected: disabled account '%s'", username)
        return jsonify({"status": "error", "code": M.CODE_ACCOUNT_DISABLED,
                        "message": M.MSG_ACCOUNT_DISABLED}), 403

    ip = row["server_ip"]

    # 2. TSplus credential check (BL: tsplus_bl)
    try:
        ts = tsplus_bl.authenticate(ip, username, password)
    except TSplusUnreachable:
        log.error("TSplus unreachable: %s", ip)
        return jsonify({"status": "error", "code": M.CODE_BACKEND_UNAVAILABLE,
                        "message": M.MSG_TSPLUS_UNREACHABLE}), 503
    except TSplusTimeout:
        log.error("TSplus timeout: %s", ip)
        return jsonify({"status": "error", "code": M.CODE_BACKEND_UNAVAILABLE,
                        "message": M.MSG_TSPLUS_TIMEOUT}), 503
    except TSplusError as e:
        log.error("TSplus auth exception for '%s': %s", username, str(e))
        return jsonify({"status": "error", "code": M.CODE_INVALID_CREDENTIALS,
                        "message": M.MSG_AUTH_FAILED}), 500

    log.info("TSplus response for '%s' (pref=%s): status=%s body=%s",
             username, preference, ts.status_code, ts.body[:200])

    if not ts.ok:
        log.warning("TSplus rejected credentials for '%s' -> %s (body: %s)",
                    username, ip, ts.body[:100])
        return jsonify({"status": "error", "code": M.CODE_INVALID_CREDENTIALS,
                        "message": M.MSG_INVALID_PASSWORD}), 401

    cookies = ts.cookies   # echoed on the response below (all 16 TSplus cookies)

    log.info("Login OK: '%s' -> %s (pref=%s ukey=%s client=%s)",
             username, ip, preference, ukey, row["client_name"])

    # 3. Build response based on preference (BL: rdp_bl for remote token issue)
    response_payload = {
        "status":     "ok",
        "preference": preference,
    }

    if preference == 'remote':
        try:
            with db() as conn:
                token = rdp_bl.issue_token(conn, username, ip)
            log.info("RDP token issued: user=%s server=%s", username, ip)
        except sqlite3.Error as e:
            log.error("Failed to issue RDP token for '%s': %s", username, e)
            return jsonify({"status": "error", "code": M.CODE_TOKEN_ISSUE_FAILED,
                            "message": M.MSG_TOKEN_ISSUE_FAILED}), 500

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


# ─── /logout ──────────────────────────────────────────────────────

@bp.route("/logout", methods=["GET", "POST"])
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
