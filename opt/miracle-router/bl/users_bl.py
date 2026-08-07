"""
BL: users resource rules.

Two responsibilities here:

1. validate_user_payload  -- field-level validation for create / update.
                             Returns (errors, cleaned). Identical
                             semantics to the inline version that lived
                             in router.py pre-Phase-5.

2. delete_by_client_cascade -- atomic cascade for the
                               DELETE /admin/users/by-client/<name>
                               flow. Lists matching users, deletes them,
                               also deletes the matching clients row,
                               and returns enough info for the
                               controller to log + JSON-respond.

Cross-resource FK existence checks (server_id_exists, client_name_exists)
stay as DAL helpers -- they're pure lookups, no rules to add on top.
"""

from datetime import date

import messages as M
from config import (
    USERNAME_RE, USER_TYPES, DATE_RE,
)
from dal import users_dal, clients_dal


# =================================================================
#  VALIDATION
# =================================================================

def validate_user_payload(data, partial=False):
    """Validate inputs for user create / update.

    Returns (errors_list, cleaned_dict). When `partial=True` (PUT),
    missing required fields are tolerated; otherwise they each
    contribute an error.
    """
    errors = []
    cleaned = {}

    required = ("username", "client_name", "server_id")
    for f in required:
        if f in data and data[f] is not None and str(data[f]).strip() != "":
            cleaned[f] = data[f]
        elif not partial:
            errors.append("Missing required field: " + f)

    if "username" in cleaned:
        u = str(cleaned["username"]).strip()
        if not USERNAME_RE.match(u):
            errors.append("username must be 1-64 chars (A-Z, a-z, 0-9, _)")
        cleaned["username"] = u

    if "client_name" in cleaned:
        c = str(cleaned["client_name"]).strip()
        if not c or len(c) > 128:
            errors.append("client_name must be 1-128 chars")
        cleaned["client_name"] = c

    # email / mobile -- REMOVED from the user API in v4.3. Contact details are
    # ACCOUNT-level only: clients.contact_email / clients.contact_mobile, both
    # of which accept several comma-separated values. Having the same concept
    # on two levels was a standing source of confusion and mis-set data.
    #
    # The physical users.email / users.mobile columns still exist (NOT NULL
    # DEFAULT ''), holding the pre-migration values, so the consolidation done
    # by migrations/v4_8_consolidate_contacts.py stays reversible. The DAL
    # writes '' on create; nothing reads them. They are NOT dropped.
    #
    # Silently ignoring a sent key would let an old EXE think it stored a
    # contact that went nowhere, so both are rejected outright.
    for gone in ("email", "mobile"):
        if gone in data:
            errors.append(M.MSG_USER_CONTACT_REMOVED_TMPL.format(gone))

    if "server_id" in cleaned:
        try:
            cleaned["server_id"] = int(cleaned["server_id"])
        except (TypeError, ValueError):
            errors.append("server_id must be an integer")

    if "is_active" in data:
        v = data["is_active"]
        if isinstance(v, bool):
            cleaned["is_active"] = 1 if v else 0
        elif v in (0, 1, "0", "1"):
            cleaned["is_active"] = int(v)
        else:
            errors.append("is_active must be 0 or 1")

    # user_type (Phase 2; 'migrated' added v4.3). Optional; defaults to 'new'
    # at the DB layer when omitted. Set by the desktop flow, not inferred from
    # the username. Accepted case-insensitively, stored lower-case.
    # The valid set is config.USER_TYPES -- the message is the single constant
    # in messages.py so the wire contract can't drift from the enum again.
    if "user_type" in data and data["user_type"] is not None \
            and str(data["user_type"]).strip() != "":
        t = str(data["user_type"]).strip().lower()
        if t not in USER_TYPES:
            errors.append(M.MSG_INVALID_USER_TYPE)
        else:
            cleaned["user_type"] = t

    # subscription_start (v4.1 as `start_date`, renamed v4.3). Optional;
    # per-user subscription/purchase start. Controller defaults it to today
    # when omitted, but a supplied value ALWAYS wins -- that is what lets a
    # migrated user keep its real back-dated start.
    #
    # WIRE NAME: `subscription_start` only. The pre-v4.3 `start_date` key is
    # NOT accepted -- a caller still sending it gets today's date by default,
    # so the desktop tool must be updated in lockstep with this deploy.
    # See docs/CHANGES_IN_EXE.md.
    if "subscription_start" in data and data["subscription_start"] is not None \
            and str(data["subscription_start"]).strip() != "":
        sd = str(data["subscription_start"]).strip()
        valid = bool(DATE_RE.match(sd))
        if valid:
            try:
                y, m, d = (int(x) for x in sd.split("-"))
                date(y, m, d)
            except ValueError:
                valid = False
        if not valid:
            errors.append("subscription_start must be a valid date in YYYY-MM-DD format")
        else:
            cleaned["subscription_start"] = sd

    return errors, cleaned


# =================================================================
#  WORKFLOWS
# =================================================================

def delete_by_client_cascade(conn, client_name):
    """Delete every user with this client_name AND the matching clients
    row (which holds the uKey). Single atomic transaction.

    The controller (and v3.4 contract) always responds 200, even when
    nothing existed -- caller distinguishes via the returned dict.

    Returns a dict:
        usernames       -- list of usernames that got deleted (may be empty)
        deleted_count   -- int, len(usernames)
        client_deleted  -- 0 or 1 (whether a clients row was removed)
        ukey            -- the deleted client's uKey, or None
        canonical_name  -- the client_name as stored in DB (case-corrected),
                           or the input client_name if no client row existed
    """
    rows       = users_dal.list_users_for_client_for_cascade(conn, client_name)
    client_row = clients_dal.get_client_brief_by_name(conn, client_name)

    if rows:
        users_dal.delete_users_by_client(conn, client_name)

    client_deleted = 0
    ukey           = None
    canonical_name = client_name
    if client_row:
        clients_dal.delete_client_by_id(conn, client_row["id"])
        client_deleted = 1
        ukey           = client_row["ukey"]
        canonical_name = client_row["client_name"]

    usernames = [r["username"] for r in rows]
    return {
        "usernames":      usernames,
        "deleted_count":  len(usernames),
        "client_deleted": client_deleted,
        "ukey":           ukey,
        "canonical_name": canonical_name,
    }
