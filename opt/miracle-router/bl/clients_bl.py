"""
BL: clients resource rules.

Two validators here. Both follow the same `(errors_list, cleaned_dict)`
contract used by users_bl and servers_bl. Sequential, mutually-exclusive
checks -- the list contains at most one entry, and the controller
surfaces it as the `message` field of the standard
{status, code, message} response (with code = CODE_VALIDATION_FAILED).
"""

import messages as M
from config import CLIENT_NAME_RE, UKEY_RE


def validate_client_create_payload(data):
    """Validate POST /admin/clients body.

    Sequential, mutually-exclusive checks. Returns (errors, cleaned).
    `errors` is empty on success, length-1 on first failure.
    """
    client_name = str(data.get("client_name", "") or "").strip()
    ukey        = str(data.get("ukey",        "") or "").strip()

    if not client_name:
        return [M.MSG_MISSING_REQ_CLIENT_NAME], {}
    if not CLIENT_NAME_RE.match(client_name):
        return [M.MSG_INVALID_CLIENT_NAME], {}
    if not ukey:
        return [M.MSG_MISSING_REQ_UKEY], {}
    if not UKEY_RE.match(ukey):
        return [M.MSG_INVALID_UKEY_FMT], {}

    return [], {"client_name": client_name, "ukey": ukey}


def validate_client_update_payload(data):
    """Validate PUT /admin/clients/<id> body.

    Both fields are optional on update -- but if present, must validate.
    Returns (errors, cleaned). The controller separately returns
    MSG_NO_FIELDS_TO_UPDATE when cleaned is empty AND errors is empty.
    """
    cleaned = {}

    if "client_name" in data:
        v = str(data["client_name"] or "").strip()
        if not v:
            return [M.MSG_CLIENT_NAME_EMPTY], {}
        if not CLIENT_NAME_RE.match(v):
            return [M.MSG_INVALID_CLIENT_NAME], {}
        cleaned["client_name"] = v

    if "ukey" in data:
        v = str(data["ukey"] or "").strip()
        if not UKEY_RE.match(v):
            return [M.MSG_INVALID_UKEY_FMT], {}
        cleaned["ukey"] = v

    return [], cleaned
