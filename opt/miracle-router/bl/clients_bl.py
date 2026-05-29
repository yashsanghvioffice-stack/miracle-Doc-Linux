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


DISPLAY_NAME_MAX = 128


def _validate_display_name(raw):
    """Shared display_name check. Returns (error_msg_or_None, cleaned_or_None).
    Allows any printable text (no regex), 1..128 chars after strip()."""
    v = str(raw or "").strip()
    if not v:
        return "display_name cannot be empty", None
    if len(v) > DISPLAY_NAME_MAX:
        return "display_name must be 1-%d chars" % DISPLAY_NAME_MAX, None
    return None, v


def validate_client_create_payload(data):
    """Validate POST /admin/clients body.

    Sequential, mutually-exclusive checks. Returns (errors, cleaned).
    `errors` is empty on success, length-1 on first failure.

    `display_name` is OPTIONAL on create. If absent or blank, the caller
    is expected to default it to client_name when persisting.
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

    cleaned = {"client_name": client_name, "ukey": ukey}

    # Optional display_name -- only validated when caller sent the key
    # AND it is non-blank. Blank/missing -> caller defaults to client_name.
    if "display_name" in data and str(data["display_name"] or "").strip():
        err, dn = _validate_display_name(data["display_name"])
        if err:
            return [err], {}
        cleaned["display_name"] = dn

    return [], cleaned


def validate_client_update_payload(data):
    """Validate PUT /admin/clients/<id> body.

    All fields are optional on update -- but if present, must validate.
    Returns (errors, cleaned). The controller separately returns
    MSG_NO_FIELDS_TO_UPDATE when cleaned is empty AND errors is empty.

    `display_name` is rename-able. Passing an empty string is rejected
    (use a real label or omit the field entirely).
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

    if "display_name" in data:
        err, dn = _validate_display_name(data["display_name"])
        if err:
            return [err], {}
        cleaned["display_name"] = dn

    return [], cleaned
