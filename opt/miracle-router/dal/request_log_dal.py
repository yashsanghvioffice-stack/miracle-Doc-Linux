"""
DAL: append-only writes to `request_log`.

One row per HTTP request handled by the gateway. Populated by the
Flask before_request/after_request hooks in `request_logging.py`.
INSERT only -- nothing in this app reads `request_log`; it's for
ops/audit (sqlite3 CLI, ad-hoc grep, future dashboards).

`ts` is written explicitly in IST (config.SQL_NOW_IST) rather than relying on
the column's UTC default, so audit timestamps match every other stored time.
"""

from config import SQL_NOW_IST


def insert(
    conn,
    method,
    path,
    status,
    duration_ms,
    client_ip,
    username,
    ukey,
    error_code,
    exception,
):
    """Append one request_log row. All columns nullable except method/path."""
    conn.execute(
        "INSERT INTO request_log "
        "(method, path, status, duration_ms, client_ip, username, ukey, error_code, exception, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, " + SQL_NOW_IST + ")",
        (
            method,
            path,
            status,
            duration_ms,
            client_ip,
            username,
            ukey,
            error_code,
            exception,
        ),
    )
