"""
BL: login bind + is_active rule.

The DAL's `users_dal.find_user_for_login` is pure SQL -- it returns a
Row or None. The business rule on top is:

    - No row     -> BindOutcome.MISS  (don't leak which sub-case)
    - is_active=0-> BindOutcome.DISABLED
    - otherwise  -> BindOutcome.OK + the joined row

That distinction is enforced here so the controller doesn't have to
re-encode it in every login flow.

Other login orchestration (calling TSplus, issuing RDP tokens, building
the HTTP response) stays in the controller because it interleaves with
HTTP concerns (request parsing, cookie setting). BL exposes the
primitives via tsplus_bl + rdp_bl; the controller composes them.
"""

from dal import users_dal


# ─── BindOutcome enum (what the bind+active check resolved to) ────

class BindOutcome:
    """Enum of what the login bind + active check resolved to (see resolve_bind):
    OK / MISS / DISABLED. MISS is deliberately identical for unknown user, wrong
    ukey, or orphan client so the response never leaks which one failed."""
    OK       = "ok"        # bind matched and is_active=1
    MISS     = "miss"      # no row -- unknown user, wrong ukey, or
                           # orphan client. Same surface response for all
                           # three (don't leak which).
    DISABLED = "disabled"  # bind matched but the user is is_active=0


# ─── Public API ───────────────────────────────────────────────────

def find_authenticated_user(conn, username, ukey):
    """Return (BindOutcome, row_or_None).

    On OK, row has (id, username, is_active, client_name, bound_ukey,
    server_ip) -- enough for both downstream auth (TSplus call) and the
    response (server_ip for routing, client_name for logging).
    """
    row = users_dal.find_user_for_login(conn, username, ukey)
    if not row:
        return (BindOutcome.MISS, None)
    if row["is_active"] != 1:
        return (BindOutcome.DISABLED, row)
    return (BindOutcome.OK, row)
