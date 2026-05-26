"""
DAL: queries against the `clients` table (tenant uKey registry).

Some functions cross-join `users` and `server_master` for enrichment
(list, exists). Those joins are read-only and stay here because the
result is per-client.
"""


def list_clients_enriched(conn):
    """List endpoint shape: one row per client, joined with the admin
    user (lowest-id user for that client) and that user's server.

    Returns Rows with: id, client_name, ukey, created_at, user_count,
    username, email, mobile, is_active, server_id, updated_at,
    server_name, server_ip.

    LEFT JOIN -- clients with no users still appear (admin user fields null).
    """
    return conn.execute("""
        SELECT c.id,
               c.client_name,
               c.ukey,
               c.created_at,
               (SELECT COUNT(*) FROM users u
                  WHERE u.client_name = c.client_name COLLATE NOCASE) AS user_count,
               u.username,
               u.email,
               u.mobile,
               u.is_active,
               u.server_id,
               u.updated_at,
               s.server_name,
               s.server_ip
        FROM   clients c
        LEFT JOIN users u ON u.id = (
            SELECT MIN(u2.id) FROM users u2
            WHERE u2.client_name = c.client_name COLLATE NOCASE
        )
        LEFT JOIN server_master s ON s.id = u.server_id
        ORDER  BY c.client_name
    """).fetchall()


def get_client_by_id(conn, client_id):
    """Single client row, or None."""
    return conn.execute(
        "SELECT * FROM clients WHERE id = ?", (client_id,)
    ).fetchone()


def get_client_by_name(conn, client_name):
    """Single client row by name (case-insensitive), or None."""
    return conn.execute(
        "SELECT * FROM clients WHERE client_name = ? COLLATE NOCASE",
        (client_name,),
    ).fetchone()


def get_client_brief_by_name(conn, client_name):
    """Existence-check projection (id, client_name, ukey) by name. NOCASE."""
    return conn.execute(
        "SELECT id, client_name, ukey FROM clients WHERE client_name = ? COLLATE NOCASE",
        (client_name,),
    ).fetchone()


def get_client_brief_by_ukey(conn, ukey):
    """Existence-check projection by uKey. NOCASE."""
    return conn.execute(
        "SELECT id, client_name, ukey FROM clients WHERE ukey = ? COLLATE NOCASE",
        (ukey,),
    ).fetchone()


def client_name_exists(conn, client_name):
    """Truthy if a row with this client_name exists (NOCASE). Used as
    FK precondition by user_create / user_update."""
    return conn.execute(
        "SELECT id FROM clients WHERE client_name = ? COLLATE NOCASE",
        (client_name,),
    ).fetchone()


def most_common_server_for_client(conn, client_name):
    """For /admin/clients/exists informational payload. Returns the
    (server_ip, server_name, n) with the most users for this client.
    None if the client has no users."""
    return conn.execute("""
        SELECT s.server_ip, s.server_name, COUNT(u.id) AS n
        FROM   users u
        JOIN   server_master s ON s.id = u.server_id
        WHERE  u.client_name = ? COLLATE NOCASE
        GROUP  BY u.server_id
        ORDER  BY n DESC
        LIMIT  1
    """, (client_name,)).fetchone()


def create_client(conn, client_name, ukey):
    """Insert and return the new row. Raises sqlite3.IntegrityError on
    duplicate client_name or ukey -- caller distinguishes via follow-up
    SELECTs (see get_client_brief_by_name / get_client_brief_by_ukey)."""
    cur = conn.execute(
        "INSERT INTO clients (client_name, ukey) VALUES (?, ?)",
        (client_name, ukey),
    )
    new_id = cur.lastrowid
    return conn.execute(
        "SELECT * FROM clients WHERE id = ?", (new_id,)
    ).fetchone()


def update_client(conn, client_id, fields):
    """Update arbitrary subset of (client_name, ukey). Returns the
    post-update row. Raises sqlite3.IntegrityError on conflicts.

    Note: the *cascade* of a client_name change into users.client_name
    is the caller's responsibility (see cascade_rename_in_users()).
    """
    sets   = ", ".join("{} = ?".format(k) for k in fields.keys())
    params = list(fields.values()) + [client_id]
    conn.execute(
        "UPDATE clients SET {} WHERE id = ?".format(sets),
        params,
    )
    return conn.execute(
        "SELECT * FROM clients WHERE id = ?", (client_id,)
    ).fetchone()


def cascade_rename_in_users(conn, old_name, new_name):
    """When a client is renamed, update every users.client_name that
    referenced the old value (denormalized FK). NOCASE on the WHERE."""
    conn.execute(
        "UPDATE users SET client_name = ? WHERE client_name = ? COLLATE NOCASE",
        (new_name, old_name),
    )


def delete_client_by_id(conn, client_id):
    """Delete a single client row. Does NOT touch users -- see
    users_dal.delete_users_by_client() for the cascade."""
    conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))


def count_users_for_client(conn, client_name):
    """Count of users with this client_name. NOCASE."""
    return conn.execute(
        "SELECT COUNT(*) FROM users WHERE client_name = ? COLLATE NOCASE",
        (client_name,),
    ).fetchone()[0]
