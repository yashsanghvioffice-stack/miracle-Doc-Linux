"""
BL: server_master resource rules.

For Phase 5 this is just the payload validator. The CRUD itself is
thin enough to live in the controller (it's just DAL calls + error-
class discrimination on IntegrityError). If a future feature needs
multi-step server orchestration, it lands here.
"""

from config import IPV4_RE, SERVER_NAME_RE


def validate_server_payload(data, partial=False):
    """Validate inputs for server create / update.

    Returns (errors_list, cleaned_dict). `errors_list` is empty on
    success; non-empty entries are user-facing strings already pulled
    from messages.py / inlined here. `cleaned_dict` contains the
    sanitized values for each field that was supplied + valid.

    When `partial=True` (used by PUT), missing fields are tolerated.
    """
    errors = []
    cleaned = {}

    if "server_name" in data and data["server_name"] is not None and str(data["server_name"]).strip() != "":
        n = str(data["server_name"]).strip()
        if not SERVER_NAME_RE.match(n):
            errors.append("server_name must be 1-64 chars (letters, digits, _ - . space)")
        cleaned["server_name"] = n
    elif not partial:
        errors.append("Missing required field: server_name")

    if "server_ip" in data and data["server_ip"] is not None and str(data["server_ip"]).strip() != "":
        ip = str(data["server_ip"]).strip()
        if not IPV4_RE.match(ip):
            errors.append("server_ip must be a valid IPv4 address")
        else:
            try:
                if any(not 0 <= int(p) <= 255 for p in ip.split(".")):
                    errors.append("server_ip octets must be 0-255")
            except ValueError:
                errors.append("server_ip octets must be numeric")
        cleaned["server_ip"] = ip
    elif not partial:
        errors.append("Missing required field: server_ip")

    return errors, cleaned
