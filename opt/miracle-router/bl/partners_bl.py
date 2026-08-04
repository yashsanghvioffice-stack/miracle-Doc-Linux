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
from config import PARTNER_NAME_RE, PHONE_RE
from bl.validators import parse_email_list


# partners.email may hold several addresses (comma-separated), like
# clients.contact_email. Cap the count; each address is format/length checked.
PARTNER_EMAIL_MAX_COUNT = 20


def _validate_name(raw):
    """Shared partner-name check. Returns (error_msg_or_None, cleaned_or_None)."""
    v = str(raw or "").strip()
    if not v:
        return M.MSG_PARTNER_NAME_EMPTY, None
    if not PARTNER_NAME_RE.match(v):
        return M.MSG_INVALID_PARTNER_NAME, None
    return None, v


def _validate_email(raw):
    """Shared partner-email check. Accepts ONE OR MORE addresses, comma-separated
    (v4.2). Returns (error_msg_or_None, cleaned_or_None). Blank -> (None, None)
    ('clear this field'); the mandatory-email callers reject empty separately.
    Each address is trimmed + lowercased + format-checked; empty segments (stray
    commas) dropped; duplicates de-duped preserving order; stored normalized as
    'a@x.com,b@y.com' (no spaces), capped at PARTNER_EMAIL_MAX_COUNT."""
    status, value = parse_email_list(raw, PARTNER_EMAIL_MAX_COUNT)
    if status == "empty":
        return None, None                  # blank -> 'clear this field'
    if status == "too_many":
        return M.MSG_TOO_MANY_PARTNER_EMAILS, None
    if status == "invalid":
        return M.MSG_INVALID_EMAIL, None
    return None, value


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

    # email is MANDATORY on create (v4.1c).
    if "email" not in data or data["email"] is None or str(data["email"]).strip() == "":
        return [M.MSG_PARTNER_EMAIL_REQUIRED], {}
    err, e = _validate_email(data["email"])
    if err:
        return [err], {}
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
    cleaned is empty AND errors is empty. Sending `phone: ""` clears phone,
    but email is MANDATORY (v4.1c) -- it can be changed but not cleared.
    """
    cleaned = {}

    if "name" in data:
        err, name = _validate_name(data["name"])
        if err:
            return [err], {}
        cleaned["name"] = name

    if "email" in data:
        # email cannot be cleared -- it's mandatory (v4.1c).
        if data["email"] is None or str(data["email"]).strip() == "":
            return [M.MSG_PARTNER_EMAIL_REQUIRED], {}
        err, e = _validate_email(data["email"])
        if err:
            return [err], {}
        cleaned["email"] = e

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
