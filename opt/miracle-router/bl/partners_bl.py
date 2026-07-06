"""
BL: partners resource rules.

Two validators. Both return the same `(errors_list, cleaned_dict)`
contract used by users_bl / clients_bl / servers_bl -- errors is empty
on success, length-1 on first failure; controller surfaces errors[0]
as the `message` field of the standard {status, code, message}
response with code = CODE_VALIDATION_FAILED.

`email` and `phone` are always optional on both create and update.
`is_active` is update-only (create defaults to 1 in the schema).
"""

import messages as M
from config import EMAIL_RE, PARTNER_NAME_RE, PHONE_RE


def _validate_name(raw):
    """Shared partner-name check. Returns (error_msg_or_None, cleaned_or_None)."""
    v = str(raw or "").strip()
    if not v:
        return M.MSG_PARTNER_NAME_EMPTY, None
    if not PARTNER_NAME_RE.match(v):
        return M.MSG_INVALID_PARTNER_NAME, None
    return None, v


def _validate_email(raw):
    """Shared email check. Returns (error_msg_or_None, cleaned_or_None).
    Empty string is treated as 'clear this field' -- valid, cleaned to None."""
    v = str(raw or "").strip()
    if not v:
        return None, None
    if not EMAIL_RE.match(v):
        return M.MSG_INVALID_EMAIL, None
    return None, v


def _validate_phone(raw):
    """Shared phone check. Returns (error_msg_or_None, cleaned_or_None).
    Empty string is treated as 'clear this field' -- valid, cleaned to None."""
    v = str(raw or "").strip()
    if not v:
        return None, None
    if not PHONE_RE.match(v):
        return M.MSG_INVALID_PHONE, None
    return None, v


def validate_partner_create_payload(data):
    """Validate POST /admin/partners body.

    Only `name` is required. `email` and `phone` are optional. Sequential,
    mutually-exclusive checks -- errors is [] or length-1.
    """
    if "name" not in data:
        return [M.MSG_MISSING_REQ_PARTNER_NAME], {}

    err, name = _validate_name(data["name"])
    if err:
        # Distinguish "empty" from "invalid format" for a clearer message.
        if err == M.MSG_PARTNER_NAME_EMPTY:
            return [M.MSG_MISSING_REQ_PARTNER_NAME], {}
        return [err], {}

    cleaned = {"name": name}

    if "email" in data:
        err, e = _validate_email(data["email"])
        if err:
            return [err], {}
        # Only include the key when it survives validation as non-None,
        # so the DAL persists NULL rather than an empty string.
        if e is not None:
            cleaned["email"] = e

    if "phone" in data:
        err, p = _validate_phone(data["phone"])
        if err:
            return [err], {}
        if p is not None:
            cleaned["phone"] = p

    return [], cleaned


def validate_partner_update_payload(data):
    """Validate PUT /admin/partners/<id> body.

    All fields optional; controller returns MSG_NO_FIELDS_TO_UPDATE when
    cleaned is empty AND errors is empty. Sending `email: ""` or
    `phone: ""` clears the field (persists NULL).
    """
    cleaned = {}

    if "name" in data:
        err, name = _validate_name(data["name"])
        if err:
            return [err], {}
        cleaned["name"] = name

    if "email" in data:
        err, e = _validate_email(data["email"])
        if err:
            return [err], {}
        cleaned["email"] = e  # may be None -> clears the column

    if "phone" in data:
        err, p = _validate_phone(data["phone"])
        if err:
            return [err], {}
        cleaned["phone"] = p  # may be None -> clears the column

    if "is_active" in data:
        v = data["is_active"]
        # Accept bool, int, or "true"/"false"/"0"/"1" strings.
        if isinstance(v, bool):
            cleaned["is_active"] = 1 if v else 0
        elif isinstance(v, int):
            if v not in (0, 1):
                return ["is_active must be 0 or 1"], {}
            cleaned["is_active"] = v
        elif isinstance(v, str):
            s = v.strip().lower()
            if s in ("1", "true", "yes"):
                cleaned["is_active"] = 1
            elif s in ("0", "false", "no"):
                cleaned["is_active"] = 0
            else:
                return ["is_active must be 0 or 1"], {}
        else:
            return ["is_active must be 0 or 1"], {}

    return [], cleaned
