"""
Miracle Cloud Gateway -- User-facing message + error code constants.

Every string returned to a client or operator in an API response lives
here. Log message templates (operator-facing) currently stay inline
in their handlers and will migrate to BL modules in later phases.

Naming convention:
    CODE_*  -- machine-readable error code (used in `code` JSON field)
    MSG_*   -- human-readable message (used in `message` JSON field
               for contract-compliant responses, or in `error` field
               for legacy responses pending Phase 7 normalization)

Some MSG_* are templates with {} placeholders -- caller .format()s them.
"""

# =================================================================
#  ERROR CODES
#  Used in contract-compliant responses: {status:"error", code:X, message:Y}
# =================================================================

CODE_MISSING_FIELDS      = "MISSING_FIELDS"
CODE_INVALID_USERNAME    = "INVALID_USERNAME"
CODE_MISSING_UKEY        = "MISSING_UKEY"
CODE_INVALID_UKEY        = "INVALID_UKEY"
CODE_INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
CODE_ACCOUNT_DISABLED    = "ACCOUNT_DISABLED"
CODE_BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
CODE_TOKEN_ISSUE_FAILED  = "TOKEN_ISSUE_FAILED"

# ─── Admin endpoint codes (added in Phase 7 to match the v3.4 contract) ──
CODE_VALIDATION_FAILED               = "VALIDATION_FAILED"
CODE_NO_FIELDS_TO_UPDATE             = "NO_FIELDS_TO_UPDATE"
CODE_CONFLICT                        = "CONFLICT"
CODE_SERVER_MISCONFIGURED            = "SERVER_MISCONFIGURED"
CODE_INVALID_API_KEY                 = "INVALID_API_KEY"

CODE_SERVER_NOT_FOUND                = "SERVER_NOT_FOUND"
CODE_SERVER_NAME_EXISTS              = "SERVER_NAME_EXISTS"
CODE_SERVER_IP_EXISTS                = "SERVER_IP_EXISTS"
CODE_CANNOT_DELETE_SERVER_WITH_USERS = "CANNOT_DELETE_SERVER_WITH_USERS"
CODE_UNKNOWN_SERVER                  = "UNKNOWN_SERVER"

CODE_CLIENT_NOT_FOUND                = "CLIENT_NOT_FOUND"
CODE_CLIENT_NAME_EXISTS              = "CLIENT_NAME_EXISTS"
CODE_INVALID_CLIENT_NAME             = "INVALID_CLIENT_NAME"
CODE_UKEY_IN_USE                     = "UKEY_IN_USE"
CODE_UNKNOWN_CLIENT                  = "UNKNOWN_CLIENT"

CODE_USER_NOT_FOUND                  = "USER_NOT_FOUND"
CODE_USERNAME_EXISTS                 = "USERNAME_EXISTS"

CODE_PARTNER_NOT_FOUND               = "PARTNER_NOT_FOUND"
CODE_PARTNER_NAME_EXISTS             = "PARTNER_NAME_EXISTS"
CODE_PARTNER_IN_USE                  = "PARTNER_IN_USE"
CODE_INVALID_PARTNER_NAME            = "INVALID_PARTNER_NAME"
CODE_INVALID_EMAIL                   = "INVALID_EMAIL"
CODE_INVALID_PHONE                   = "INVALID_PHONE"


# =================================================================
#  USER-FACING MESSAGES -- contract-compliant (login + RDP flow)
# =================================================================

MSG_USERNAME_REQUIRED   = "Username required"
MSG_INVALID_USERNAME    = "Invalid username format"
MSG_PASSWORD_REQUIRED   = "Password required"
MSG_MISSING_UKEY        = "Access link required."
MSG_INVALID_UKEY        = "Invalid access link."

# Same CODE_INVALID_CREDENTIALS, three distinct messages by sub-case
# (intentional: surface stays uniform; messages differ slightly for UX)
MSG_INVALID_CREDENTIALS = "Invalid access link or credentials."
MSG_INVALID_PASSWORD    = "Invalid username or password"
MSG_AUTH_FAILED         = "Authentication failed"

MSG_ACCOUNT_DISABLED    = "Account disabled. Contact your administrator."
MSG_TSPLUS_UNREACHABLE  = "Authentication server unreachable"
MSG_TSPLUS_TIMEOUT      = "Authentication server timed out"
MSG_TOKEN_ISSUE_FAILED  = "Could not prepare download. Please try again."


# =================================================================
#  MESSAGES used by admin endpoints (paired with the CODE_* above).
#  Pre-Phase-7 these were returned as {"error": "..."}. Post-Phase-7
#  they all flow through the standard {status, code, message} shape.
# =================================================================

# ─── Auth / config ────────────────────────────────────────────────
MSG_SERVER_MISCONFIGURED            = "Server misconfigured: API key not set"
MSG_INVALID_API_KEY                 = "Invalid or missing API key"

# ─── Validation ───────────────────────────────────────────────────
MSG_VALIDATION_FAILED               = "Validation failed"
MSG_NO_FIELDS_TO_UPDATE             = "No fields to update"
MSG_CONFLICT                        = "Conflict"

# ─── Server resource ──────────────────────────────────────────────
MSG_SERVER_NAME_EXISTS              = "server_name already exists"
MSG_SERVER_IP_EXISTS                = "server_ip already exists"
MSG_SERVER_NOT_FOUND                = "Server not found"
MSG_CANNOT_DELETE_SERVER_WITH_USERS = "Cannot delete server while users reference it"
MSG_DELETE_USERS_HINT               = "Delete or reassign those users first"
MSG_SERVER_ID_NOT_EXIST_TMPL        = "server_id {} does not exist"

# ─── Client resource ──────────────────────────────────────────────
MSG_CLIENT_NOT_FOUND                = "Client not found"
MSG_CLIENT_NAME_EXISTS              = "client_name already exists"
MSG_CLIENT_NAME_EMPTY               = "client_name cannot be empty"
MSG_INVALID_CLIENT_NAME             = "client_name must be 1-64 chars [A-Za-z0-9_-]"
MSG_INVALID_CLIENT_NAME_SHORT       = "invalid client_name"
MSG_MISSING_REQ_CLIENT_NAME         = "Missing required field: client_name"
MSG_MISSING_REQ_UKEY                = "Missing required field: ukey"
MSG_INVALID_UKEY_FMT                = "ukey must be 8 alphanumeric chars"
MSG_UKEY_IN_USE                     = "ukey already in use"
MSG_UNKNOWN_CLIENT                  = "Unknown client. Create it via POST /admin/clients first."
MSG_UNKNOWN_CLIENT_HINT_TMPL        = "Create the client first via POST /admin/clients with client_name='{}'"

# ─── User resource ────────────────────────────────────────────────
MSG_USER_NOT_FOUND                  = "User not found"
MSG_USERNAME_EXISTS                 = "Username already exists"

# ─── Partner resource ─────────────────────────────────────────────
MSG_PARTNER_NOT_FOUND               = "Partner not found"
MSG_PARTNER_NAME_EXISTS             = "Partner name already exists"
MSG_PARTNER_NAME_EMPTY              = "Partner name cannot be empty"
MSG_INVALID_PARTNER_NAME            = "Partner name must be 1-128 chars"
MSG_MISSING_REQ_PARTNER_NAME        = "Missing required field: name"
MSG_INVALID_EMAIL                   = "Invalid email format"
MSG_INVALID_PHONE                   = "phone must be 7-20 chars (digits, spaces, +, -, parens)"
MSG_PARTNER_IN_USE                  = "Partner is referenced by one or more clients"
MSG_PARTNER_IN_USE_HINT             = "Deactivate (is_active=0) instead of deleting, or reassign clients first."


# =================================================================
#  STARTUP / FATAL MESSAGES
#  Used by verify_schema() at app boot. Templates with {} placeholders.
# =================================================================

MSG_DB_NOT_FOUND_TMPL = (
    "FATAL: Database file not found at {}\n"
    "       Bootstrap the schema first:\n"
    "         sudo python3 /opt/miracle-router/init_db.py"
)

MSG_DB_CANNOT_OPEN_TMPL = "FATAL: Cannot open database at {}: {}"

MSG_DB_MISSING_TABLES_TMPL = (
    "FATAL: Required tables missing in {}: {}\n"
    "       Run: sudo python3 /opt/miracle-router/init_db.py"
)
