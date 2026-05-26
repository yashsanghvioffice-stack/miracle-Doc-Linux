"""
Miracle Cloud Gateway -- Configuration

All environment-derived values, paths, timeouts, regex patterns, and
compile-time constants. This file does NOT depend on Flask, logging,
the DB, or any other gateway module -- safe to import from anywhere.
"""

import os
import re

# ─── Environment ──────────────────────────────────────────────────
DB_PATH = os.environ.get("MIRACLE_DB_PATH", "/etc/miracle-registry/miracle.db")
API_KEY = os.environ.get("MIRACLE_API_KEY", "")

# ─── Paths ────────────────────────────────────────────────────────
LOG_PATH = "/var/log/miracle-router.log"

# ─── Timeouts ─────────────────────────────────────────────────────
TSPLUS_TIMEOUT        = 10           # seconds
RDP_TOKEN_TTL_SECONDS = 300          # 5 minutes

# ─── Schema ───────────────────────────────────────────────────────
REQUIRED_TABLES = ("server_master", "users", "rdp_download_tokens", "clients", "request_log")

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
