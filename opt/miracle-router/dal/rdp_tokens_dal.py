"""
DAL: queries against `rdp_download_tokens`.

Tokens are single-use, time-limited credentials that let a browser
download a .rdp file once after a successful login. The DAL exposes
the four primitives the login + download flows need.
"""


def cleanup_expired(conn, ttl_seconds):
    """Lazy cleanup: delete tokens older than the TTL. Idempotent."""
    conn.execute(
        "DELETE FROM rdp_download_tokens "
        "WHERE created_at < datetime('now', '-' || ? || ' seconds')",
        (ttl_seconds,),
    )


def issue_token(conn, token, username, server_ip):
    """Insert a new (token, username, server_ip, created_at=now) row."""
    conn.execute(
        "INSERT INTO rdp_download_tokens "
        "(token, username, server_ip, created_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (token, username, server_ip),
    )


def get_token(conn, token):
    """Return the token row with a computed `age_seconds` column,
    or None if no such token. Caller decides expiry / single-use.
    """
    return conn.execute("""
        SELECT username, server_ip, used_at, created_at,
               (strftime('%s','now') - strftime('%s', created_at)) AS age_seconds
        FROM   rdp_download_tokens
        WHERE  token = ?
    """, (token,)).fetchone()


def mark_token_used(conn, token):
    """Atomically mark a token consumed by setting used_at=now."""
    conn.execute(
        "UPDATE rdp_download_tokens SET used_at = datetime('now') WHERE token = ?",
        (token,),
    )
