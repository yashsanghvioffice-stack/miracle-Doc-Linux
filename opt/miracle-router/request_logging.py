"""
Per-request audit logging.

Wraps every Flask request with before/after/teardown hooks that:
  - capture method, path, status, duration_ms
  - extract username + ukey best-effort from POST body / cookies
  - on exception: capture full Python stack trace
  - write one INFO line per request to /var/log/miracle-router.log
    (ERROR line + stack trace on exceptions)
  - append one row to the `request_log` table

Designed for ops audit. One INSERT per request is fine at gateway scale
(SQLite WAL, low write volume). /health is excluded to avoid noise.

Failures in the logging path are swallowed -- a logging bug must never
break a real request.
"""

import time
import traceback

from flask import g, request

from config import UKEY_COOKIE_NAME
from dal.connection import db
from dal import request_log_dal
from logger import log


SKIP_PATHS = {"/health"}


def _extract_user_ukey():
    """Best-effort username + ukey extraction.

    /login POST body is the canonical source. For other routes the ukey
    cookie is the only thing we can read without a DB lookup. Username
    on non-/login routes is `None` -- TSplus owns the session, not us.
    """
    username = None
    ukey = None

    if request.method == "POST" and request.path == "/login":
        if request.is_json:
            data = request.get_json(silent=True) or {}
        else:
            data = request.form
        username = (data.get("username") or "").strip() or None
        ukey = (data.get("ukey") or "").strip() or None

    if not ukey:
        ukey = request.cookies.get(UKEY_COOKIE_NAME) or None

    return username, ukey


def _client_ip():
    """Prefer X-Forwarded-For (set by nginx); fall back to remote_addr."""
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip() or None
    return request.remote_addr


def install_request_logging(app):
    """Register the three Flask hooks on `app`."""

    @app.before_request
    def _start_timer():
        g._req_start  = time.perf_counter()
        g._req_status = None

    @app.after_request
    def _capture_status(response):
        g._req_status = response.status_code
        return response

    @app.teardown_request
    def _persist(exc):
        try:
            path = request.path
            if path in SKIP_PATHS:
                return

            start = getattr(g, "_req_start", None)
            duration_ms = int((time.perf_counter() - start) * 1000) if start else None
            status = getattr(g, "_req_status", None)
            method = request.method
            ip = _client_ip()
            username, ukey = _extract_user_ukey()

            exception_text = None
            if exc is not None:
                exception_text = "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )

            line = "REQ %s %s status=%s %sms ip=%s user=%s ukey=%s" % (
                method,
                path,
                status if status is not None else "-",
                duration_ms if duration_ms is not None else "-",
                ip or "-",
                username or "-",
                ukey or "-",
            )
            if exc is not None:
                log.error("%s\n%s", line, exception_text)
            else:
                log.info(line)

            try:
                with db() as conn:
                    request_log_dal.insert(
                        conn,
                        method=method,
                        path=path,
                        status=status,
                        duration_ms=duration_ms,
                        client_ip=ip,
                        username=username,
                        ukey=ukey,
                        error_code=None,
                        exception=exception_text,
                    )
            except Exception as db_err:
                # DB insert failed -- file log is already written; stay quiet.
                log.error("request_log insert failed: %s", db_err)
        except Exception as e:
            # Logging must never break a real request.
            try:
                log.error("request_logging hook failed: %s", e)
            except Exception:
                pass
