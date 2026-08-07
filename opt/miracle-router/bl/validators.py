"""
Shared BL validation helpers.

Cross-resource validation logic that would otherwise be copy-pasted between
`clients_bl`, `partners_bl`, `users_bl`. Each helper is pure (no Flask, no DB)
and returns a neutral result the caller maps to its own error messages, so the
rule lives in ONE place while each resource keeps its own wording.
"""

from config import EMAIL_RE, MOBILE_RE

EMAIL_MAX_LEN = 254


def is_valid_email(addr):
    """True if `addr` (already trimmed/lowercased by the caller) is a
    syntactically valid, length-bounded email address."""
    return bool(EMAIL_RE.match(addr)) and len(addr) <= EMAIL_MAX_LEN


def normalize_mobile(raw):
    """Strip spaces/dashes from `raw` and check the 7-15 digit pattern.
    Returns `(ok, cleaned)`. The caller handles the empty/clear case."""
    m = str(raw).strip().replace(" ", "").replace("-", "")
    return bool(MOBILE_RE.match(m)), m


def parse_email_list(raw, max_count):
    """Parse a comma-separated email list (one or more addresses).

    Trims + lowercases each address, drops empty segments (stray/trailing
    commas), de-dupes preserving order, and caps the count. Returns
    `(status, value)`:

        ("ok", "a@x.com,b@y.com")  normalized, comma-joined, no spaces
        ("empty", None)            blank input (caller decides if allowed)
        ("invalid", None)          a malformed/oversized address, or none valid
        ("too_many", None)         more than `max_count` distinct addresses

    The caller maps status -> its own MSG_* text (contact_email vs partner
    email use different wording but the same rule).
    """
    v = str(raw or "").strip()
    if not v:
        return "empty", None
    seen, emails = set(), []
    for part in v.split(","):
        e = part.strip().lower()
        if not e:
            continue                       # tolerate stray/trailing commas
        if not is_valid_email(e):
            return "invalid", None
        if e not in seen:
            seen.add(e)
            emails.append(e)
    if not emails:
        return "invalid", None
    if len(emails) > max_count:
        return "too_many", None
    return "ok", ",".join(emails)


def parse_mobile_list(raw, max_count):
    """Parse a comma-separated mobile list (one or more numbers).

    The mobile twin of parse_email_list, added in v4.3 when per-user contacts
    were consolidated onto the client: a client with three users can end up
    holding three numbers, and a single-value column could not represent that
    without discarding data. Same contract, same statuses.

    Each number is trimmed of spaces/dashes and checked against the 7-15 digit
    rule; empty segments are dropped; duplicates removed preserving order.

        ("ok", "+911111111111,+912222222222")
        ("empty", None) / ("invalid", None) / ("too_many", None)

    A single number still round-trips unchanged, so every pre-v4.3 caller is
    unaffected -- this only widens what is accepted.
    """
    v = str(raw or "").strip()
    if not v:
        return "empty", None
    seen, mobiles = set(), []
    for part in v.split(","):
        if not part.strip():
            continue                       # tolerate stray/trailing commas
        ok, m = normalize_mobile(part)
        if not ok:
            return "invalid", None
        if m not in seen:
            seen.add(m)
            mobiles.append(m)
    if not mobiles:
        return "invalid", None
    if len(mobiles) > max_count:
        return "too_many", None
    return "ok", ",".join(mobiles)
