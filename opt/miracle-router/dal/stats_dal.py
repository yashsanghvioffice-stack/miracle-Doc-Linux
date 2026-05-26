"""
DAL: aggregate queries for /health and /admin/stats.

Both functions take a live `conn` and return raw values / Rows. The
caller composes the JSON response shape (BL/controller responsibility).
"""


def health_snapshot(conn):
    """Counts for the /health endpoint.

    Returns dict with keys: users, active, servers, total_clients, pending_rdp_tokens.
    """
    users    = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active   = conn.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
    srvs     = conn.execute("SELECT COUNT(*) FROM server_master").fetchone()[0]
    clients_count = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
    tokens   = conn.execute(
        "SELECT COUNT(*) FROM rdp_download_tokens WHERE used_at IS NULL"
    ).fetchone()[0]
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
    total    = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active   = conn.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
    srvs     = conn.execute("SELECT COUNT(*) FROM server_master").fetchone()[0]
    clients_count = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
    clients_dist  = conn.execute("SELECT COUNT(DISTINCT client_name) FROM users").fetchone()[0]
    pending  = conn.execute(
        "SELECT COUNT(*) FROM rdp_download_tokens WHERE used_at IS NULL"
    ).fetchone()[0]
    consumed = conn.execute(
        "SELECT COUNT(*) FROM rdp_download_tokens WHERE used_at IS NOT NULL"
    ).fetchone()[0]
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
