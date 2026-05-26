"""
DAL: queries against `server_master` (the TSplus host registry).

Functions return `sqlite3.Row` (or None) and raise `sqlite3.IntegrityError`
on UNIQUE constraint violations -- caller distinguishes server_name vs
server_ip clashes from the exception message.
"""


def list_servers(conn):
    """All servers, ordered by id, each with a user_count subquery."""
    return conn.execute("""
        SELECT s.*,
               (SELECT COUNT(*) FROM users u WHERE u.server_id = s.id) AS user_count
        FROM   server_master s
        ORDER  BY s.id
    """).fetchall()


def get_server_by_id(conn, server_id):
    """Single server row, or None."""
    return conn.execute(
        "SELECT * FROM server_master WHERE id = ?", (server_id,)
    ).fetchone()


def server_id_exists(conn, server_id):
    """Lightweight FK precondition check. Returns truthy if the row exists."""
    return conn.execute(
        "SELECT id FROM server_master WHERE id = ?", (server_id,)
    ).fetchone()


def create_server(conn, server_name, server_ip):
    """Insert and return the new row. Raises sqlite3.IntegrityError on
    duplicate server_name or server_ip."""
    cur = conn.execute(
        "INSERT INTO server_master (server_name, server_ip) VALUES (?, ?)",
        (server_name, server_ip),
    )
    new_id = cur.lastrowid
    return conn.execute(
        "SELECT * FROM server_master WHERE id = ?", (new_id,)
    ).fetchone()


def update_server(conn, server_id, fields):
    """Update arbitrary subset of fields. Also bumps updated_at.

    `fields` is a dict of column->value pairs already validated by the caller.
    Returns the post-update row. Raises sqlite3.IntegrityError on conflicts.
    """
    sets   = ", ".join("{} = ?".format(k) for k in fields.keys())
    params = list(fields.values()) + [server_id]
    conn.execute(
        "UPDATE server_master SET {}, updated_at = datetime('now') WHERE id = ?".format(sets),
        params,
    )
    return conn.execute(
        "SELECT * FROM server_master WHERE id = ?", (server_id,)
    ).fetchone()


def delete_server_by_id(conn, server_id):
    """Delete the row. Caller must already have checked FK constraints
    (no users referencing this server)."""
    conn.execute("DELETE FROM server_master WHERE id = ?", (server_id,))
