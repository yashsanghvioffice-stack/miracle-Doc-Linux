"""
DAL: append-only writes to `request_log`.

One row per HTTP request handled by the gateway. Populated by the
Flask before_request/after_request hooks in `request_logging.py`.
INSERT only -- nothing in this app reads `request_log`; it's for
ops/audit (sqlite3 CLI, ad-hoc grep, future dashboards).
"""


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
        "(method, path, status, duration_ms, client_ip, username, ukey, error_code, exception) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
