"""
BL: clients resource rules.

Two validators here. Both follow the same `(errors_list, cleaned_dict)`
contract used by users_bl and servers_bl. Sequential, mutually-exclusive
checks -- the list contains at most one entry, and the controller
surfaces it as the `message` field of the standard
{status, code, message} response (with code = CODE_VALIDATION_FAILED).
"""

from datetime import date, timedelta

import messages as M
from config import CLIENT_NAME_RE, UKEY_RE, DATE_RE, SUBSCRIPTION_TYPES


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


# ─── Phase 2: subscription dates + partner reference ──────────────

def _parse_iso_date(iso_str):
    """Parse a 'YYYY-MM-DD' string into a datetime.date. Raises
    ValueError on an out-of-range calendar date (e.g. 2026-13-40)."""
    y, m, d = (int(x) for x in iso_str.split("-"))
    return date(y, m, d)


def _validate_date(raw, field_name):
    """Shape + calendar validity check for a wire date. Returns
    (error_msg_or_None, iso_string_or_None). Rejects both malformed
    strings and impossible calendar dates."""
    v = str(raw or "").strip()
    if not DATE_RE.match(v):
        return M.MSG_INVALID_DATE_TMPL.format(field_name), None
    try:
        _parse_iso_date(v)
    except ValueError:
        return M.MSG_INVALID_DATE_TMPL.format(field_name), None
    return None, v


def add_one_year_minus_one_day(iso_start):
    """Auto-calc subscription_end from a start date: start + 1 year − 1 day.
    e.g. 2026-06-29 -> 2027-06-28. Input and output are ISO strings.

    Feb-29 starts are clamped to Feb-28 of the following (non-leap) year
    before subtracting the day -- a rare edge that never arises for the
    real created-at-derived dates the backfill produces."""
    start = _parse_iso_date(iso_start)
    try:
        plus_year = start.replace(year=start.year + 1)
    except ValueError:                       # start is Feb 29
        plus_year = start.replace(year=start.year + 1, day=28)
    return (plus_year - timedelta(days=1)).isoformat()


def _apply_optional_client_fields(data, cleaned):
    """Validate and fold the Phase-2 optional client fields (partner_id,
    subscription_start, subscription_end) into `cleaned`. Shared by the
    create and update validators.

    null / empty / missing are all treated identically as 'not provided'
    -- the field is simply left out of `cleaned` (no clearing semantics).
    Returns an error message string on the first bad value, else None.
    """
    if "partner_id" in data and data["partner_id"] is not None \
            and str(data["partner_id"]).strip() != "":
        try:
            cleaned["partner_id"] = int(data["partner_id"])
        except (TypeError, ValueError):
            return M.MSG_INVALID_PARTNER_ID

    for field in ("subscription_start", "subscription_end"):
        if field in data and data[field] is not None and str(data[field]).strip() != "":
            err, iso = _validate_date(data[field], field)
            if err:
                return err
            cleaned[field] = iso

    # subscription_type (v4.1) -- 'single' | 'multi', case-insensitive.
    if "subscription_type" in data and data["subscription_type"] is not None \
            and str(data["subscription_type"]).strip() != "":
        v = str(data["subscription_type"]).strip().lower()
        if v not in SUBSCRIPTION_TYPES:
            return M.MSG_INVALID_SUBSCRIPTION_TYPE
        cleaned["subscription_type"] = v

    # storage_gb (v4.1) -- positive integer (total shared HARD quota).
    if "storage_gb" in data and data["storage_gb"] is not None \
            and str(data["storage_gb"]).strip() != "":
        try:
            n = int(data["storage_gb"])
        except (TypeError, ValueError):
            return M.MSG_INVALID_STORAGE_GB
        if n <= 0:
            return M.MSG_INVALID_STORAGE_GB
        cleaned["storage_gb"] = n

    return None


def apply_subscription_rules(cleaned, is_create):
    """Inject the client EXPIRY (subscription_end) default/auto-calc after
    validation.

    v4.1: subscription_start is NOT persisted on the client (per-user start
    lives on users.start_date). A caller may still pass subscription_start
    as the *basis* for the expiry auto-calc, but it is dropped before the
    client is written.

    * create: if subscription_end absent, calc = (provided start or today)
      + 1yr − 1day.
    * update: only auto-calc subscription_end when a new start was sent
      without an end (store-as-sent otherwise).
    """
    if is_create:
        if "subscription_end" not in cleaned:
            base = cleaned.get("subscription_start") or date.today().isoformat()
            cleaned["subscription_end"] = add_one_year_minus_one_day(base)
    else:
        if "subscription_start" in cleaned and "subscription_end" not in cleaned:
            cleaned["subscription_end"] = add_one_year_minus_one_day(
                cleaned["subscription_start"])

    # subscription_start is never written on the client (deprecated column).
    cleaned.pop("subscription_start", None)


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

    # Optional Phase-2 fields (partner_id, subscription dates). The
    # controller applies defaults/auto-calc + partner existence after this.
    err = _apply_optional_client_fields(data, cleaned)
    if err:
        return [err], {}

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

    # Optional Phase-2 fields (partner_id, subscription dates). The
    # controller checks partner existence + auto-calcs end after this.
    err = _apply_optional_client_fields(data, cleaned)
    if err:
        return [err], {}

    return [], cleaned
