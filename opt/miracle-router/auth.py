"""
HTTP-layer helpers shared by every controller.

Two small utilities, both with hard dependencies on Flask (request /
jsonify). They don't belong in BL (no business rules) and they don't
belong in DAL (no SQL). They live here because every controller
needs them.

    require_api_key  -- decorator for /admin/* endpoints (extracts
                        X-API-Key header, returns 401/500 on missing/
                        wrong key)
    parse_body       -- accepts JSON or form-encoded bodies uniformly,
                        returns a dict
"""

from functools import wraps

from flask import jsonify, request

import messages as M
from config import API_KEY
from logger import log


def require_api_key(fn):
    """Wrap an /admin/* handler. Rejects requests without a valid
    X-API-Key header. Returns 500 if the server has no key configured
    (defensive -- should never happen in a deployed install).

    Returns the v3.4 contracted error shape:
        {"status": "error", "code": "...", "message": "..."}
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        provided = request.headers.get("X-API-Key", "")
        if not API_KEY:
            log.error("MIRACLE_API_KEY env var not set -- refusing all admin requests.")
            return jsonify({"status": "error",
                            "code":    M.CODE_SERVER_MISCONFIGURED,
                            "message": M.MSG_SERVER_MISCONFIGURED}), 500
        if provided != API_KEY:
            return jsonify({"status": "error",
                            "code":    M.CODE_INVALID_API_KEY,
                            "message": M.MSG_INVALID_API_KEY}), 401
        return fn(*args, **kwargs)
    return wrapper


def parse_body():
    """Accept JSON or form-encoded bodies uniformly. Always returns a
    dict (empty if neither parse path produced one)."""
    j = request.get_json(silent=True)
    if j and isinstance(j, dict):
        return j
    return request.form.to_dict()
