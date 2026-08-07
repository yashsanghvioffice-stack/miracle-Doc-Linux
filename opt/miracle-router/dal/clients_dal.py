"""
DAL: queries against the `clients` table (tenant uKey registry).

Some functions cross-join `users` and `server_master` for enrichment
(list, exists). Those joins are read-only and stay here because the
result is per-client.

CLIENT_SELECT is the canonical single-client read (Phase 2): clients
LEFT JOIN partners so every get/create/update returns partner_name
alongside the raw partner_id. LEFT JOIN keeps clients with no partner
(partner_id NULL) and clients whose partner was soft-deleted visible.
"""

from config import SQL_NOW_IST
from dal.connection import apply_update


CLIENT_SELECT = """
    SELECT c.*,
           p.name AS partner_name
    FROM   clients c
    LEFT JOIN partners p ON p.id = c.partner_id
"""


def list_clients_enriched(conn):
    """List endpoint shape: one row per client, joined with the admin
    user (lowest-id user for that client), that user's server, and the
    account's partner.

    Returns Rows with: id, client_name, display_name, ukey, partner_id,
    partner_name, subscription_type, storage_gb, subscription_start,
    subscription_end, contact_email, contact_mobile, legacy_server_name,
    legacy_server_ip, created_at, user_count, username, email, mobile,
    is_active, server_id, updated_at, server_name, server_ip.

    Note: server_name/server_ip are the CURRENT routing target (via the
    admin user's server). legacy_server_name/legacy_server_ip are where a
    migrated customer came FROM -- different columns, different meaning.

    LEFT JOIN -- clients with no users still appear (admin user fields null).
    """
    # display_name is COALESCE'd to client_name so legacy rows (created
    # before the column existed) still return a usable label.
    return conn.execute("""
        SELECT c.id,
               c.client_name,
               COALESCE(c.display_name, c.client_name) AS display_name,
               c.ukey,
               c.partner_id,
               p.name AS partner_name,
               c.subscription_type,
               c.storage_gb,
               -- subscription_end only: the start is per-user
               -- (users.subscription_start), the expiry is shared per client.
               c.subscription_end,
               c.contact_email,
               c.contact_mobile,
               c.legacy_server_name,
               c.legacy_server_ip,
               c.created_at,
               -- v4.3. Exposed as client_updated_at, NOT updated_at: the
               -- `updated_at` key on this endpoint has always been the admin
               -- USER's (part of the u.* contact block below), and silently
               -- changing what it means would break existing readers.
               c.updated_at AS client_updated_at,
               (SELECT COUNT(*) FROM users u
                  WHERE u.client_name = c.client_name COLLATE NOCASE) AS user_count,
               u.username,
               -- v4.3: the admin user's email/mobile are NO LONGER returned
               -- here. This row used to carry FOUR contact fields -- email +
               -- mobile (the admin user's) sitting beside contact_email +
               -- contact_mobile (the account's) -- which was a standing source
               -- of confusion about which one was authoritative. Contacts are
               -- account-level only now: c.contact_email / c.contact_mobile.
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
        LEFT JOIN partners p ON p.id = c.partner_id
        ORDER  BY c.client_name
    """).fetchall()


def get_client_by_id(conn, client_id):
    """Single client row (+ partner_name), or None."""
    return conn.execute(
        CLIENT_SELECT + " WHERE c.id = ?", (client_id,)
    ).fetchone()


def get_client_by_name(conn, client_name):
    """Single client row (+ partner_name) by name (case-insensitive), or None."""
    return conn.execute(
        CLIENT_SELECT + " WHERE c.client_name = ? COLLATE NOCASE",
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


def create_client(conn, client_name, ukey, display_name=None,
                  partner_id=None, subscription_end=None,
                  subscription_type=None, storage_gb=None,
                  contact_email=None, contact_mobile=None,
                  legacy_server_name=None, legacy_server_ip=None):
    """Insert and return the new row (+ partner_name via CLIENT_SELECT).

    `display_name` is optional; when None the column is left NULL and read
    endpoints fall back to client_name via COALESCE. `partner_id`,
    `subscription_end` (expiry), `subscription_type`, and `storage_gb`
    are optional -- the controller defaults/validates them before calling.

    Note: `subscription_start` is NOT written here -- the start date is
    per-user (users.subscription_start). `subscription_end` (the one shared
    expiry) stays client-level and unchanged.

    `legacy_server_name` / `legacy_server_ip` (v4.3) record where a migrated
    customer came FROM. NULL for normal signups. They are history, not
    routing -- the CURRENT target remains users.server_id. WRITE-ONCE: this
    is the only place they are written; update_client never sets them.

    Raises sqlite3.IntegrityError on duplicate client_name or ukey --
    caller distinguishes via follow-up SELECTs (see
    get_client_brief_by_name / get_client_brief_by_ukey)."""
    cur = conn.execute("""
        INSERT INTO clients
            (client_name, display_name, ukey, partner_id,
             subscription_end, subscription_type,
             storage_gb, contact_email, contact_mobile,
             legacy_server_name, legacy_server_ip, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {now})
    """.format(now=SQL_NOW_IST), (client_name, display_name, ukey, partner_id,
          subscription_end, subscription_type,
          storage_gb, contact_email, contact_mobile,
          legacy_server_name, legacy_server_ip))
    new_id = cur.lastrowid
    return conn.execute(
        CLIENT_SELECT + " WHERE c.id = ?", (new_id,)
    ).fetchone()


def update_client(conn, client_id, fields):
    """Update arbitrary subset of (client_name, display_name, ukey,
    partner_id, subscription_end, subscription_type, storage_gb,
    contact_email, contact_mobile). Returns the post-update row
    (+ partner_name). Raises sqlite3.IntegrityError on conflicts.

    Write-once columns are NOT updatable here: subscription_start,
    legacy_server_name and legacy_server_ip are set at creation only. The
    update validator never puts them in `fields`, so a later call cannot
    silently rewrite a migrated customer's origin or original start date.

    Note: the *cascade* of a client_name change into users.client_name
    is the caller's responsibility (see cascade_rename_in_users()).
    """
    # v4.3: routed through the shared helper so clients.updated_at is bumped
    # to IST on every edit, exactly like users / servers / partners. Before
    # this, `clients` was the only table that tracked created_at but never
    # recorded when a row last changed.
    apply_update(conn, "clients", fields, client_id)
    return conn.execute(
        CLIENT_SELECT + " WHERE c.id = ?", (client_id,)
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
