"""
BL: TSplus integration -- the hb.exe credential check.

Encapsulates everything about talking to TSplus: building the cookie
fingerprint, the URL-encoded payload, the headers, the timeout, and
the response interpretation.

Returns a TSplusResult on transport success (whether credentials were
accepted or not). Raises a specific exception on transport-level
failure so the caller can map to the right HTTP status code.

Critical TSplus quirks preserved here (see feedback-tsplus-quirks
in operator memory):
  - Username is lowercased on the way out (TSplus does this internally)
  - All 16 fingerprint cookies must be present in the request
  - Referer must be set
"""

import time
from urllib.parse import quote

import requests as req

from config import TSPLUS_REQUEST_COOKIES, TSPLUS_TIMEOUT


# ─── Exceptions ───────────────────────────────────────────────────

class TSplusUnreachable(Exception):
    """ConnectionError -- backend is down or routing is broken."""


class TSplusTimeout(Exception):
    """Request timed out (TSPLUS_TIMEOUT exceeded)."""


class TSplusError(Exception):
    """Any other transport-layer failure. Carries the underlying str."""


# ─── Result type ──────────────────────────────────────────────────

class TSplusResult:
    """Outcome of authenticate(). Transport-level success.

    Fields:
        ok         -- True iff TSplus replied with Status:ok (creds valid)
        cookies    -- the request cookies dict. Caller echoes these
                      onto the HTTP response (TSplus needs all 16 set
                      on the browser; see TSPLUS_REQUEST_COOKIES).
        status_code -- raw HTTP status from TSplus (for logging)
        body        -- response body (truncated by caller if needed)
    """
    __slots__ = ("ok", "cookies", "status_code", "body")

    def __init__(self, ok, cookies, status_code, body):
        self.ok          = ok
        self.cookies     = cookies
        self.status_code = status_code
        self.body        = body


# ─── Public API ───────────────────────────────────────────────────

def authenticate(server_ip, username, password):
    """POST credentials to http://<server_ip>/cgi-bin/hb.exe.

    Returns TSplusResult on transport success (regardless of whether
    TSplus accepted the password). Raises TSplusUnreachable /
    TSplusTimeout / TSplusError on transport failure.
    """
    cookies = dict(TSPLUS_REQUEST_COOKIES)
    cookies["username_Editbox1"] = username

    base_url  = "http://{}".format(server_ip)
    timestamp = str(int(time.time() * 1000))
    payload   = "action=cp&l={}&p={}&d=&f=&t={}".format(
        # TSplus internally lowercases the username during auth.
        # Matching here is non-negotiable.
        username.lower(),
        quote(password, safe=""),
        timestamp,
    )

    try:
        resp = req.post(
            "{}/cgi-bin/hb.exe".format(base_url),
            data=payload,
            headers={
                "Content-Type"   : "text/plain;charset=UTF-8",
                "Host"           : server_ip,
                "Origin"         : base_url,
                "Referer"        : "{}/".format(base_url),
                "User-Agent"     : "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:149.0) Gecko/20100101 Firefox/149.0",
                "Accept"         : "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection"     : "keep-alive",
            },
            cookies=cookies,
            timeout=TSPLUS_TIMEOUT,
        )
    except req.exceptions.ConnectionError as e:
        raise TSplusUnreachable(str(e))
    except req.exceptions.Timeout as e:
        raise TSplusTimeout(str(e))
    except Exception as e:
        raise TSplusError(str(e))

    # Interpret the response. TSplus returns JSON with {"Status": "ok"}
    # on credential success, anything else means rejected.
    try:
        body_json = resp.json()
        status    = body_json.get("Status", "").lower()
    except Exception:
        status = ""

    return TSplusResult(
        ok=(status == "ok"),
        cookies=cookies,
        status_code=resp.status_code,
        body=resp.text,
    )
