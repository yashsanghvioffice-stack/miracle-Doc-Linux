"""
DAL: queries against `rdp_download_tokens`.

Tokens are single-use, time-limited credentials that let a browser
download a .rdp file once after a successful login. The DAL exposes
the four primitives the login + download flows need.

created_at is stored in IST (see config.SQL_NOW_IST), so every 'now' used to
compare against it is also shifted +5:30 -- otherwise a freshly issued token
would read a negative age and never expire.
"""

from config import SQL_NOW_IST, SQL_IST_SHIFT


def cleanup_expired(conn, ttl_seconds):
    """Lazy cleanup: delete tokens older than the TTL. Idempotent."""
    conn.execute(
        "DELETE FROM rdp_download_tokens "
        "WHERE created_at < datetime('now', " + SQL_IST_SHIFT + ", '-' || ? || ' seconds')",
        (ttl_seconds,),
    )


def issue_token(conn, token, username, server_ip):
    """Insert a new (token, username, server_ip, created_at=now-IST) row."""
    conn.execute(
        "INSERT INTO rdp_download_tokens "
        "(token, username, server_ip, created_at) "
        "VALUES (?, ?, ?, " + SQL_NOW_IST + ")",
        (token, username, server_ip),
    )


def get_token(conn, token):
    """Return the token row with a computed `age_seconds` column,
    or None if no such token. Caller decides expiry / single-use.
    """
    return conn.execute("""
        SELECT username, server_ip, used_at, created_at,
               (strftime('%s','now',{shift}) - strftime('%s', created_at)) AS age_seconds
        FROM   rdp_download_tokens
        WHERE  token = ?
    """.format(shift=SQL_IST_SHIFT), (token,)).fetchone()


def mark_token_used(conn, token):
    """Atomically mark a token consumed by setting used_at=now."""
    conn.execute(
        "UPDATE rdp_download_tokens SET used_at = " + SQL_NOW_IST + " WHERE token = ?",
        (token,),
    )
