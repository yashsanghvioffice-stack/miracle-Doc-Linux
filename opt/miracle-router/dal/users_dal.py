"""
DAL: queries against the `users` table.

USER_SELECT is the canonical joined read (users + server_master + clients
LEFT JOIN). Every read endpoint returns rows in this shape so callers
get a stable column set including ukey and server info.
"""


# ─── The shared joined SELECT ─────────────────────────────────────
# Used by every user-read endpoint. Includes:
#   - users columns (id, username, client_name, email, mobile, server_id,
#                    is_active, created_at, updated_at)
#   - server_master columns (server_name, server_ip)
#   - clients column (ukey)   -- LEFT JOIN so orphaned users still appear
#
USER_SELECT = """
    SELECT u.id, u.username, u.client_name, u.email, u.mobile,
           u.server_id, u.is_active, u.created_at, u.updated_at,
           s.server_name, s.server_ip,
           c.ukey,
           COALESCE(c.display_name, c.client_name) AS display_name
    FROM   users u
    JOIN   server_master s ON s.id = u.server_id
    LEFT JOIN clients c ON c.client_name = u.client_name COLLATE NOCASE
"""


# =================================================================
#  READS
# =================================================================

def get_user_by_id(conn, user_id):
    """Single user Row (joined) or None."""
    return conn.execute(USER_SELECT + " WHERE u.id = ?", (user_id,)).fetchone()


def list_users(conn, active_only=False, client_name=None, server_id=None):
    """List users with optional filters. Ordered by u.id."""
    where  = []
    params = []
    if active_only:
        where.append("u.is_active = 1")
    if client_name:
        where.append("u.client_name = ?")
        params.append(client_name)
    if server_id:
        where.append("u.server_id = ?")
        params.append(server_id)

    query = USER_SELECT
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY u.id"
    return conn.execute(query, params).fetchall()


def find_user_for_login(conn, username, ukey):
    """The login bind query. Returns a Row with (id, username, is_active,
    client_name, bound_ukey, server_ip) -- enough for both auth checks
    AND the TSplus call. None on miss.

    Matching is exact on username, NOCASE on the ukey JOIN.
    """
    return conn.execute("""
        SELECT u.id, u.username, u.is_active,
               u.client_name, c.ukey AS bound_ukey,
               s.server_ip
        FROM   users u
        JOIN   clients       c ON c.client_name = u.client_name COLLATE NOCASE
        JOIN   server_master s ON s.id = u.server_id
        WHERE  u.username = ?
          AND  c.ukey     = ? COLLATE NOCASE
    """, (username, ukey)).fetchone()


def user_id_exists(conn, user_id):
    """Truthy if the user row exists. Returns Row or None."""
    return conn.execute(
        "SELECT id FROM users WHERE id = ?", (user_id,)
    ).fetchone()


def get_user_username(conn, user_id):
    """Just the username column. Used by delete pre-check for logging."""
    return conn.execute(
        "SELECT username FROM users WHERE id = ?", (user_id,)
    ).fetchone()


def list_users_for_client(conn, client_name):
    """Light projection of users for a client. Used by client-detail
    endpoints (id, username, is_active only, ordered by username)."""
    return conn.execute(
        "SELECT id, username, is_active FROM users "
        "WHERE client_name = ? COLLATE NOCASE ORDER BY username",
        (client_name,),
    ).fetchall()


def list_users_for_client_for_cascade(conn, client_name):
    """For the cascade-delete endpoint. Returns (id, username) Rows for
    every user with this client_name. Case-SENSITIVE match -- mirrors
    the historical v3.4 contract."""
    return conn.execute(
        "SELECT id, username FROM users WHERE client_name = ?", (client_name,)
    ).fetchall()


def count_users_for_server(conn, server_id):
    """How many users currently reference this server_id. FK precondition
    for server delete."""
    return conn.execute(
        "SELECT COUNT(*) FROM users WHERE server_id = ?", (server_id,)
    ).fetchone()[0]


# =================================================================
#  WRITES
# =================================================================

def create_user(conn, username, client_name, email, mobile, server_id, is_active=1):
    """Insert a user. Returns the new joined Row (via USER_SELECT).
    Raises sqlite3.IntegrityError on duplicate username."""
    cur = conn.execute("""
        INSERT INTO users (username, client_name, email, mobile, server_id, is_active)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (username, client_name, email, mobile, server_id, is_active))
    new_id = cur.lastrowid
    return conn.execute(USER_SELECT + " WHERE u.id = ?", (new_id,)).fetchone()


def update_user(conn, user_id, fields):
    """Update arbitrary subset of user columns. Bumps updated_at.
    Returns the post-update joined Row. Raises sqlite3.IntegrityError
    on conflicts (e.g. duplicate username)."""
    sets   = ", ".join("{} = ?".format(k) for k in fields.keys())
    params = list(fields.values()) + [user_id]
    conn.execute(
        "UPDATE users SET {}, updated_at = datetime('now') WHERE id = ?".format(sets),
        params,
    )
    return conn.execute(USER_SELECT + " WHERE u.id = ?", (user_id,)).fetchone()


def set_user_active(conn, user_id, is_active):
    """Flip the is_active flag (1 or 0). Bumps updated_at. Returns the
    post-update joined Row."""
    conn.execute(
        "UPDATE users SET is_active = ?, updated_at = datetime('now') WHERE id = ?",
        (is_active, user_id),
    )
    return conn.execute(USER_SELECT + " WHERE u.id = ?", (user_id,)).fetchone()


def delete_user_by_id(conn, user_id):
    """Delete one user row."""
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def delete_users_by_client(conn, client_name):
    """Bulk delete of every user for a client. Case-SENSITIVE on the
    WHERE -- mirrors the historical v3.4 cascade semantics."""
    conn.execute("DELETE FROM users WHERE client_name = ?", (client_name,))
