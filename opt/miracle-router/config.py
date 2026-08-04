"""
Miracle Cloud Gateway -- Configuration

All environment-derived values, paths, timeouts, regex patterns, and
compile-time constants. This file does NOT depend on Flask, logging,
the DB, or any other gateway module -- safe to import from anywhere.
"""

import os
import re
from datetime import datetime, timedelta, timezone

# ─── Environment ──────────────────────────────────────────────────
DB_PATH = os.environ.get("MIRACLE_DB_PATH", "/etc/miracle-registry/miracle.db")
API_KEY = os.environ.get("MIRACLE_API_KEY", "")

# ─── Time zone (IST) ──────────────────────────────────────────────
# Every timestamp/date the gateway stores or returns is IST (UTC+5:30; India
# observes no DST). SQLite's CURRENT_TIMESTAMP / datetime('now') and Python's
# naive now()/today() are UTC / host-dependent, so we apply a FIXED +5:30
# offset everywhere -- values are correct regardless of the server's OS clock.
IST_TZ        = timezone(timedelta(hours=5, minutes=30))
# The one source of truth for the UTC->IST shift, as SQLite date-function
# modifiers. Everything else composes from this (DRY) -- if the offset ever
# changes, change it here only.
SQL_IST_SHIFT = "'+5 hours', '+30 minutes'"
# SQL fragments -- constants, safe to embed directly in query strings.
SQL_NOW_IST   = "datetime('now', %s)" % SQL_IST_SHIFT   # 'YYYY-MM-DD HH:MM:SS'
SQL_TODAY_IST = "date('now', %s)" % SQL_IST_SHIFT       # 'YYYY-MM-DD'


def now_ist():
    """Current IST timestamp as 'YYYY-MM-DD HH:MM:SS' (for Python-side writes)."""
    return datetime.now(IST_TZ).strftime("%Y-%m-%d %H:%M:%S")


def today_ist():
    """Today's date in IST as 'YYYY-MM-DD'."""
    return datetime.now(IST_TZ).date().isoformat()

# v4.1c: require partner_id on client create. Partner is mandatory per the
# RA, so this defaults ON. During the EXE/PS Setup rollout window (before
# those clients send a partner) set MIRACLE_REQUIRE_PARTNER=0 to relax it
# temporarily and avoid breaking provisioning. Read at request time so it
# can be toggled without a restart in tests.
REQUIRE_PARTNER = os.environ.get("MIRACLE_REQUIRE_PARTNER", "1").strip().lower() in ("1", "true", "yes")

# ─── Paths ────────────────────────────────────────────────────────
LOG_PATH = "/var/log/miracle-router.log"

# ─── Timeouts ─────────────────────────────────────────────────────
TSPLUS_TIMEOUT        = 10           # seconds
RDP_TOKEN_TTL_SECONDS = 300          # 5 minutes

# ─── Schema ───────────────────────────────────────────────────────
REQUIRED_TABLES = ("server_master", "users", "rdp_download_tokens", "clients", "request_log", "partners")

# ─── TSplus / RDP ─────────────────────────────────────────────────
TSPLUS_RDP_PORT = 59359           # same for all TSplus servers

# ─── uKey cookie (JS-readable, long-lived) ────────────────────────
UKEY_COOKIE_NAME    = "miracle_ukey"
UKEY_COOKIE_MAX_AGE = 30 * 24 * 3600   # 30 days

# ─── Redirect targets per login preference ────────────────────────
REDIRECT_HTML5  = "/workspace"
REDIRECT_REMOTE = "/workspace-remote"

# ─── Validation patterns ──────────────────────────────────────────
USERNAME_RE    = re.compile(r"^[A-Za-z0-9_]{1,64}$")
EMAIL_RE       = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
MOBILE_RE      = re.compile(r"^\+?[0-9]{7,15}$")
IPV4_RE        = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\. ]{1,64}$")
TOKEN_RE       = re.compile(r"^[a-f0-9]{32}$")
UKEY_RE        = re.compile(r"^[A-Za-z0-9]{8}$")
CLIENT_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

# Partner name: 1-128 chars. Allow letters, digits, spaces, and common
# punctuation seen in real company names (& . , ' - _ ( ) / +).
PARTNER_NAME_RE = re.compile(r"^[A-Za-z0-9 &.,'\-_()/+]{1,128}$")

# Partner / client contact phone: 7-20 chars of digits, spaces, +, -, (, ).
PHONE_RE       = re.compile(r"^[0-9 +\-()]{7,20}$")

# Wire date format: ISO YYYY-MM-DD (the desktop app formats DD-MM-YYYY
# for display only). Shape check only -- calendar validity (rejecting
# 2026-13-40) is done by constructing a datetime.date in the BL layer.
DATE_RE        = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# user_type enum (Phase 2). 'new' = created in a Create-a-Customer batch,
# 'additional' = added to an existing account later. Set by the flow, not
# inferred from the username.
USER_TYPES     = ("new", "additional")

# subscription_type enum (v4.1). 'single' vs 'multi' user subscription,
# chosen once at Setup and stored on the client.
SUBSCRIPTION_TYPES = ("single", "multi")

# ─── TSplus browser fingerprint cookies ───────────────────────────
# DO NOT change. Required by TSplus for /login auth -- all 16 keys
# must be present in the request to hb.exe and set on the response.
# See feedback-tsplus-quirks in operator memory.
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

# ─── RDP template ─────────────────────────────────────────────────
# Substituted per request via .format(server_ip=..., rdp_port=..., username=...).
# Windows .rdp files use CRLF line endings.
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
