"""
DAL: queries against the `partners` table (master partner list).

Partners are a global master picked from the desktop-app Setup dropdown.
They are NOT re-entered per client; a Phase-2 client stores partner_id
and inherits the partner's name/email at read time via LEFT JOIN.

Phase 1 ships the partners table AND the clients.partner_id column
(nullable, no FK yet). The endpoints that let a client SELECT a partner
land in Phase 2. That means Phase 1's count_clients_for_partner will
return 0 for every partner (nothing references them yet) which is the
correct answer.

Deletion policy is enforced by the controller, not here:
    * partner referenced by any client -> soft delete (is_active=0)
    * unreferenced                      -> hard delete
"""

from config import SQL_NOW_IST
from dal.connection import apply_update


def list_partners(conn, active_only=False):
    """List partners, ordered by name. When active_only is truthy, filters
    to is_active=1. Each row also carries a client_count subquery so the
    dashboard can show 'X clients on this partner' without a follow-up
    round trip."""
    where = "WHERE p.is_active = 1" if active_only else ""
    return conn.execute("""
        SELECT p.id,
               p.name,
               p.email,
               p.phone,
               p.is_active,
               p.created_at,
               p.updated_at,
               (SELECT COUNT(*) FROM clients c WHERE c.partner_id = p.id) AS client_count
        FROM   partners p
        {where}
        ORDER  BY p.name COLLATE NOCASE
    """.format(where=where)).fetchall()


def get_partner_by_id(conn, partner_id):
    """Single partner row, or None."""
    return conn.execute(
        "SELECT * FROM partners WHERE id = ?", (partner_id,)
    ).fetchone()


def get_partner_by_name(conn, name):
    """Single partner row by name (case-insensitive), or None. Used for
    duplicate-name detection on POST."""
    return conn.execute(
        "SELECT * FROM partners WHERE name = ? COLLATE NOCASE",
        (name,),
    ).fetchone()


def create_partner(conn, name, email=None, phone=None):
    """Insert and return the new row. Raises sqlite3.IntegrityError on
    duplicate name (unique constraint on partners.name)."""
    cur = conn.execute(
        "INSERT INTO partners (name, email, phone, created_at) "
        "VALUES (?, ?, ?, " + SQL_NOW_IST + ")",
        (name, email, phone),
    )
    new_id = cur.lastrowid
    return conn.execute(
        "SELECT * FROM partners WHERE id = ?", (new_id,)
    ).fetchone()


def update_partner(conn, partner_id, fields):
    """Update arbitrary subset of (name, email, phone, is_active). Also
    bumps updated_at. Returns the post-update row. Raises
    sqlite3.IntegrityError on name conflict."""
    apply_update(conn, "partners", fields, partner_id)
    return conn.execute(
        "SELECT * FROM partners WHERE id = ?", (partner_id,)
    ).fetchone()


def soft_delete_partner(conn, partner_id):
    """Set is_active=0 without removing the row. Historical client
    references keep working (name/email still readable via JOIN)."""
    apply_update(conn, "partners", {"is_active": 0}, partner_id)


def delete_partner_by_id(conn, partner_id):
    """Hard delete. Caller must have confirmed no clients reference
    this partner_id."""
    conn.execute("DELETE FROM partners WHERE id = ?", (partner_id,))


def count_clients_for_partner(conn, partner_id):
    """How many clients currently reference this partner_id. Used by the
    DELETE controller to pick soft- vs hard-delete."""
    return conn.execute(
        "SELECT COUNT(*) FROM clients WHERE partner_id = ?",
        (partner_id,),
    ).fetchone()[0]
