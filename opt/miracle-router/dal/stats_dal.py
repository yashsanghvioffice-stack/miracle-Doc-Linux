"""
DAL: aggregate queries for /health and /admin/stats.

Both functions take a live `conn` and return raw values / Rows. The
caller composes the JSON response shape (BL/controller responsibility).
"""


def _scalar(conn, sql):
    """Run a single-value query and return that value (the `.fetchone()[0]`
    boilerplate, in one place)."""
    return conn.execute(sql).fetchone()[0]


def health_snapshot(conn):
    """Counts for the /health endpoint.

    Returns dict with keys: users, active, servers, total_clients, pending_rdp_tokens.
    """
    users    = _scalar(conn, "SELECT COUNT(*) FROM users")
    active   = _scalar(conn, "SELECT COUNT(*) FROM users WHERE is_active=1")
    srvs     = _scalar(conn, "SELECT COUNT(*) FROM server_master")
    clients_count = _scalar(conn, "SELECT COUNT(*) FROM clients")
    tokens   = _scalar(conn, "SELECT COUNT(*) FROM rdp_download_tokens WHERE used_at IS NULL")
    return {
        "users":               users,
        "active":              active,
        "servers":             srvs,
        "total_clients":       clients_count,
        "pending_rdp_tokens":  tokens,
    }


def admin_stats_snapshot(conn):
    """Counts for /admin/stats. Includes per-server user breakdown.

    Returns dict with all counts + a `per_server` list of Rows.
    """
    total    = _scalar(conn, "SELECT COUNT(*) FROM users")
    active   = _scalar(conn, "SELECT COUNT(*) FROM users WHERE is_active=1")
    srvs     = _scalar(conn, "SELECT COUNT(*) FROM server_master")
    clients_count = _scalar(conn, "SELECT COUNT(*) FROM clients")
    clients_dist  = _scalar(conn, "SELECT COUNT(DISTINCT client_name) FROM users")
    pending  = _scalar(conn, "SELECT COUNT(*) FROM rdp_download_tokens WHERE used_at IS NULL")
    consumed = _scalar(conn, "SELECT COUNT(*) FROM rdp_download_tokens WHERE used_at IS NOT NULL")
    per_srv  = conn.execute("""
        SELECT s.id, s.server_name, s.server_ip,
               COUNT(u.id) AS user_count
        FROM   server_master s
        LEFT   JOIN users u ON u.server_id = s.id
        GROUP  BY s.id
        ORDER  BY s.id
    """).fetchall()
    return {
        "total_users":         total,
        "active_users":        active,
        "disabled_users":      total - active,
        "total_servers":       srvs,
        "total_clients":       clients_count,
        "distinct_clients":    clients_dist,
        "rdp_tokens_pending":  pending,
        "rdp_tokens_consumed": consumed,
        "per_server":          per_srv,
    }
