"""
BL: RDP token lifecycle + .rdp file rendering.

Wraps the DAL with the business rules around single-use, time-limited
download tokens for the RemoteApp .rdp flow.

issue_token() handles the random generation + opportunistic cleanup
of expired rows. consume_token() encapsulates the test-and-set
expiry/used-once semantics and returns a TokenOutcome enum so the
caller can map to HTTP responses.
"""

import secrets

from config import RDP_TEMPLATE, RDP_TOKEN_TTL_SECONDS, TSPLUS_RDP_PORT
from dal import rdp_tokens_dal


# ─── TokenOutcome enum (the four states consume_token can return) ─

class TokenOutcome:
    """Enum of the four states consume_token can return: OK (valid + just-consumed),
    NOT_FOUND, ALREADY_USED, or EXPIRED."""
    OK            = "ok"            # token valid + just-consumed; caller may serve .rdp
    NOT_FOUND     = "not_found"     # no such token in the DB
    ALREADY_USED  = "already_used"  # used_at is non-NULL
    EXPIRED       = "expired"       # age > RDP_TOKEN_TTL_SECONDS


# ─── Public API ───────────────────────────────────────────────────

def build_rdp_content(server_ip, username):
    """Substitute the per-user values into the .rdp template.

    The TSplus port is hardcoded in config (same for every TSplus host).
    """
    return RDP_TEMPLATE.format(
        server_ip=server_ip,
        rdp_port=TSPLUS_RDP_PORT,
        username=username,
    )


def issue_token(conn, username, server_ip):
    """Generate a fresh single-use token, prune expired rows, insert.
    Returns the token string for inclusion in the login response."""
    token = secrets.token_hex(16)
    rdp_tokens_dal.cleanup_expired(conn, RDP_TOKEN_TTL_SECONDS)
    rdp_tokens_dal.issue_token(conn, token, username, server_ip)
    return token


def consume_token(conn, token):
    """Atomically validate + mark-used. Returns (outcome, row_or_None).

    On TokenOutcome.OK the row contains (username, server_ip, ...) for
    the .rdp file content. On all other outcomes, the caller should
    return an error response (404 for NOT_FOUND, 410 for ALREADY_USED
    and EXPIRED -- the controller picks the HTTP code).
    """
    row = rdp_tokens_dal.get_token(conn, token)
    if not row:
        return (TokenOutcome.NOT_FOUND, None)
    if row["used_at"] is not None:
        return (TokenOutcome.ALREADY_USED, row)
    if row["age_seconds"] is not None and row["age_seconds"] > RDP_TOKEN_TTL_SECONDS:
        return (TokenOutcome.EXPIRED, row)
    rdp_tokens_dal.mark_token_used(conn, token)
    return (TokenOutcome.OK, row)
