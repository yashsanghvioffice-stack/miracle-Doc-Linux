"""
DAL: queries against the `users` table.

USER_SELECT is the canonical joined read (users + server_master + clients
LEFT JOIN). Every read endpoint returns rows in this shape so callers
get a stable column set including ukey and server info.
"""

from config import SQL_NOW_IST
from dal.connection import apply_update


# ─── The shared joined SELECT ─────────────────────────────────────
# Used by every user-read endpoint. Includes:
#   - users columns (id, username, client_name, email, mobile, server_id,
#                    is_active, user_type, created_at, updated_at)
#   - server_master columns (server_name, server_ip)
#   - clients columns (ukey, display_name) -- LEFT JOIN so orphaned users
#     still appear
#   - account-level Phase-2 fields the User-wise Report needs, pulled from
#     the user's client + that client's partner: partner_id, partner_name,
#     subscription_start, subscription_end. This is THE single query that
#     drives the report -- no per-row round trips.
#
USER_SELECT = """
    SELECT u.id, u.username, u.client_name,
           NULLIF(u.email, '')  AS email,
           NULLIF(u.mobile, '') AS mobile,
           u.server_id, u.is_active, u.user_type, u.start_date,
           u.created_at, u.updated_at,
           s.server_name, s.server_ip,
           c.id AS client_id,
           c.ukey,
           COALESCE(c.display_name, c.client_name) AS display_name,
           c.partner_id,
           p.name AS partner_name,
           c.subscription_type,
           c.storage_gb,
           c.subscription_end
    FROM   users u
    JOIN   server_master s ON s.id = u.server_id
    LEFT JOIN clients c ON c.client_name = u.client_name COLLATE NOCASE
    LEFT JOIN partners p ON p.id = c.partner_id
"""
# (USER_SELECT is defined above; c.id is exposed as client_id there so the
#  per-user rows carry the CLIENT id alongside the user id.)


# =================================================================
#  READS
# =================================================================

def get_user_by_id(conn, user_id):
    """Single user Row (joined) or None."""
    return conn.execute(USER_SELECT + " WHERE u.id = ?", (user_id,)).fetchone()


# ─── Shared filter builder (used by both list_users + list_users_grouped) ──
# References aliases u (users), c (clients), p (partners) -- present in both
# USER_SELECT and GROUPED_SELECT. Never references server_master, so it is
# safe in the grouped query which doesn't join it.

def _user_filter(active_only=False, client_name=None, server_id=None,
                 user_type=None, partner=None, search=None, status=None):
    """Return (where_clause, params) for the report/user-list filters.

    status ('active'|'deactive'|'inactive'|'all') takes precedence over the
    legacy active_only bool. partner is a name substring; search matches
    across client_name / display_name / partner name / ukey.
    """
    where, params = [], []

    s = (status or "").strip().lower()
    if s == "active":
        where.append("u.is_active = 1")
    elif s in ("deactive", "inactive"):
        where.append("u.is_active = 0")
    elif active_only:
        where.append("u.is_active = 1")

    if client_name:
        where.append("u.client_name = ?")
        params.append(client_name)
    if server_id:
        where.append("u.server_id = ?")
        params.append(server_id)
    if user_type and str(user_type).strip():
        where.append("u.user_type = ?")
        params.append(str(user_type).strip().lower())
    if partner and str(partner).strip():
        where.append("p.name LIKE ?")
        params.append("%" + str(partner).strip() + "%")
    if search and str(search).strip():
        term = "%" + str(search).strip() + "%"
        where.append("(u.client_name LIKE ? "
                     "OR COALESCE(c.display_name, c.client_name) LIKE ? "
                     "OR p.name LIKE ? OR c.ukey LIKE ?)")
        params += [term, term, term, term]

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return clause, params


def list_users(conn, **filters):
    """List individual users (per-user rows) with optional filters.
    Ordered by created_at DESC (newest-created first), u.id DESC as the
    same-second tiebreaker. This is the `expand=true` shape."""
    clause, params = _user_filter(**filters)
    return conn.execute(
        USER_SELECT + clause + " ORDER BY u.created_at DESC, u.id DESC", params
    ).fetchall()


# ─── Grouped report (v4.1b) ───────────────────────────────────────
# One row per (client × user_type × start_date) -- a "purchase batch".
# no_of_users = count in the group; total_users = the client's grand total
# (correlated subquery, filter-INdependent); active/inactive split within
# the group. This is the DEFAULT `GET /admin/users` shape.

GROUPED_SELECT = """
    SELECT u.client_name,
           c.id AS id,
           COALESCE(c.display_name, c.client_name) AS display_name,
           c.ukey,
           c.partner_id,
           p.name AS partner_name,
           c.subscription_type,
           c.storage_gb,
           c.subscription_end,
           u.user_type,
           u.start_date,
           COUNT(*)                                        AS no_of_users,
           SUM(CASE WHEN u.is_active = 1 THEN 1 ELSE 0 END) AS active_users,
           SUM(CASE WHEN u.is_active = 0 THEN 1 ELSE 0 END) AS inactive_users,
           (SELECT COUNT(*) FROM users ut
              WHERE ut.client_name = u.client_name COLLATE NOCASE) AS total_users
    FROM   users u
    LEFT JOIN clients  c ON c.client_name = u.client_name COLLATE NOCASE
    LEFT JOIN partners p ON p.id = c.partner_id
"""


def list_users_grouped(conn, **filters):
    """Grouped report rows. Same filters as list_users."""
    clause, params = _user_filter(**filters)
    query = (GROUPED_SELECT + clause +
             " GROUP BY u.client_name COLLATE NOCASE, u.user_type, u.start_date"
             # Newly-created batch on top: order groups by their most-recent
             # user created_at; MAX(u.id) breaks ties (same-second creates)
             # so the order is always deterministic.
             " ORDER BY MAX(u.created_at) DESC, MAX(u.id) DESC")
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

def create_user(conn, username, client_name, email, mobile, server_id,
                is_active=1, user_type="new", start_date=None):
    """Insert a user. Returns the new joined Row (via USER_SELECT).
    Raises sqlite3.IntegrityError on duplicate username.

    `user_type` (Phase 2) is 'new' or 'additional' -- the desktop app
    sends the right value based on the flow; the DAL just stores it.

    `start_date` (v4.1) is this user's subscription/purchase start date
    (ISO YYYY-MM-DD). The controller defaults it to today when omitted.

    `email`/`mobile` (v4.2) are OPTIONAL: None is coerced to '' so the
    NOT NULL columns are satisfied; USER_SELECT surfaces '' back as null."""
    email  = email  or ""
    mobile = mobile or ""
    cur = conn.execute("""
        INSERT INTO users
            (username, client_name, email, mobile, server_id, is_active,
             user_type, start_date, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, {now})
    """.format(now=SQL_NOW_IST), (username, client_name, email, mobile, server_id,
          is_active, user_type, start_date))
    new_id = cur.lastrowid
    return conn.execute(USER_SELECT + " WHERE u.id = ?", (new_id,)).fetchone()


def update_user(conn, user_id, fields):
    """Update arbitrary subset of user columns. Bumps updated_at.
    Returns the post-update joined Row. Raises sqlite3.IntegrityError
    on conflicts (e.g. duplicate username)."""
    apply_update(conn, "users", fields, user_id)
    return conn.execute(USER_SELECT + " WHERE u.id = ?", (user_id,)).fetchone()


def set_user_active(conn, user_id, is_active):
    """Flip the is_active flag (1 or 0). Bumps updated_at. Returns the
    post-update joined Row."""
    apply_update(conn, "users", {"is_active": is_active}, user_id)
    return conn.execute(USER_SELECT + " WHERE u.id = ?", (user_id,)).fetchone()


def delete_user_by_id(conn, user_id):
    """Delete one user row."""
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def delete_users_by_client(conn, client_name):
    """Bulk delete of every user for a client. Case-SENSITIVE on the
    WHERE -- mirrors the historical v3.4 cascade semantics."""
    conn.execute("DELETE FROM users WHERE client_name = ?", (client_name,))
