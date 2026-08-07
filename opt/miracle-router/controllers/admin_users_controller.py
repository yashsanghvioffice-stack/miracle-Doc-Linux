"""
Admin endpoints for users:
    POST   /admin/users
    GET    /admin/users
    GET    /admin/users/<id>
    PUT    /admin/users/<id>
    DELETE /admin/users/<id>
    POST   /admin/users/<id>/enable
    POST   /admin/users/<id>/disable
    DELETE /admin/users/by-client/<name>   -- cascade: users + their clients row
"""

import sqlite3

from flask import Blueprint, jsonify, request
from responses import error

import config

import messages as M
from logger import log

from auth import require_api_key, parse_body
from dal.connection import db
from dal import users_dal, servers_dal, clients_dal
from bl import users_bl
from bl.users_bl import validate_user_payload


bp = Blueprint("admin_users", __name__, url_prefix="/admin/users")


@bp.route("", methods=["POST"])
@require_api_key
def user_create():
    """POST /admin/users -- create one user. Required: username, client_name
    (must already exist), server_id. `email`/`mobile` are OPTIONAL (v4.2).
    Returns 201 with the joined row; 400 UNKNOWN_SERVER / UNKNOWN_CLIENT /
    VALIDATION_FAILED; 409 USERNAME_EXISTS on a duplicate username."""
    data = parse_body()
    errors, cleaned = validate_user_payload(data, partial=False)
    if errors:
        return error(M.CODE_VALIDATION_FAILED, M.MSG_VALIDATION_FAILED, 400, details=errors)

    with db() as conn:
        if not servers_dal.server_id_exists(conn, cleaned["server_id"]):
            return error(M.CODE_UNKNOWN_SERVER, M.MSG_SERVER_ID_NOT_EXIST_TMPL.format(cleaned['server_id']), 400)

        # client_name MUST exist in clients table (uKey precondition)
        if not clients_dal.client_name_exists(conn, cleaned["client_name"]):
            return error(M.CODE_UNKNOWN_CLIENT, M.MSG_UNKNOWN_CLIENT, 400, hint=M.MSG_UNKNOWN_CLIENT_HINT_TMPL.format(cleaned["client_name"]))

        try:
            row = users_dal.create_user(
                conn,
                cleaned["username"],
                cleaned["client_name"],
                # v4.3: contacts are account-level only. The columns are still
                # NOT NULL, so '' satisfies the schema while nothing reads them.
                "", "",
                cleaned["server_id"],
                cleaned.get("is_active", 1),
                cleaned.get("user_type", "new"),
                # v4.1: per-user subscription start (renamed from start_date
                # in v4.3); default to today so every user of one
                # Setup/Add-Users event shares a date. A supplied value wins.
                cleaned.get("subscription_start") or config.today_ist(),
            )
        except sqlite3.IntegrityError as e:
            # A CHECK-constraint failure is NOT a duplicate username. The only
            # reachable CHECK on `users` is the user_type enum (is_active is
            # already constrained to 0/1 by the BL), and a DB whose `users`
            # table was CREATEd before v4.3 still carries the 2-value
            # CHECK(user_type IN ('new','additional')) -- SQLite cannot ALTER
            # it. Without this branch that surfaces as a 409 "Username already
            # exists", which is actively misleading. See init_db.py.
            if "check constraint" in str(e).lower():
                log.error("user_type CHECK rejected value %r -- this DB predates "
                          "v4.3 and needs the widened CHECK on `users`: %s",
                          cleaned.get("user_type", "new"), e)
                return error(M.CODE_VALIDATION_FAILED, M.MSG_INVALID_USER_TYPE, 400, detail=str(e))
            return error(M.CODE_USERNAME_EXISTS, M.MSG_USERNAME_EXISTS, 409, detail=str(e))

    log.info("User created: id=%s username=%s", row["id"], cleaned['username'])
    return jsonify(dict(row)), 201


@bp.route("", methods=["GET"])
@require_api_key
def user_list():
    """User-wise Report (v4.1b).

    DEFAULT: grouped rows -- one per (client × user_type × subscription_start) --
    plus a `summary` block for the dashboard cards. This is the report grain.

    `expand=true`: individual per-user rows (the pre-v4.1b shape, kept for
    callers that need the raw list).

    Filters (both modes): user_type, status (all|active|deactive), partner
    (name substring), client_name, server_id, search (Account/Customer ID/
    Partner/Ukey). Legacy `active_only` still honoured.
    """
    a = request.args
    expand  = a.get("expand", "").lower() in ("1", "true", "yes")
    filters = dict(
        active_only = a.get("active_only", "").lower() in ("1", "true", "yes"),
        client_name = a.get("client_name"),
        server_id   = a.get("server_id"),
        user_type   = a.get("user_type"),
        partner     = a.get("partner"),
        search      = a.get("search"),
        status      = a.get("status"),
    )

    with db() as conn:
        if expand:
            rows = users_dal.list_users(conn, **filters)
            return jsonify({"users": [dict(r) for r in rows], "count": len(rows)})

        grouped = users_dal.list_users_grouped(conn, **filters)
        out = []
        for r in grouped:
            d = dict(r)
            active   = d.get("active_users") or 0
            inactive = d.get("inactive_users") or 0
            # O1: badge is 'active' only when none are inactive; 'inactive'
            # when none are active; 'mixed' otherwise. Cards count users.
            d["status"] = ("active" if active and not inactive
                           else "inactive" if not active
                           else "mixed")
            out.append(d)

    # Per-type user counts (v4.3). PURELY ADDITIVE -- the four pre-existing
    # totals are unchanged, and total_users still counts every user
    # regardless of type (migrated users ARE users). The three type counts
    # sum to total_users, so a mismatch is a visible bug.
    #
    # 'migrated' is deliberately NOT folded into any "new" figure: a migrated
    # customer is not new business, and conflating them is the exact
    # misreporting this feature exists to prevent.
    #
    # Like every figure here these respect the active filters, so
    # ?user_type=migrated reports new_users=0 -- consistent with how
    # total_users already behaves.
    def _users_of_type(t):
        return sum(d.get("no_of_users") or 0 for d in out
                   if (d.get("user_type") or "") == t)

    summary = {
        "total_customer_ids": len({(d["client_name"] or "").lower() for d in out}),
        "total_users":        sum(d.get("no_of_users")     or 0 for d in out),
        "active_users":       sum(d.get("active_users")    or 0 for d in out),
        "deactive_users":     sum(d.get("inactive_users")  or 0 for d in out),
        "new_users":          _users_of_type("new"),
        "additional_users":   _users_of_type("additional"),
        "migrated_users":     _users_of_type("migrated"),
    }
    return jsonify({"summary": summary, "rows": out, "count": len(out)})


@bp.route("/<int:user_id>", methods=["GET"])
@require_api_key
def user_get(user_id):
    """GET /admin/users/<id> -- one joined user row; 404 USER_NOT_FOUND on miss."""
    with db() as conn:
        row = users_dal.get_user_by_id(conn, user_id)
    if not row:
        return error(M.CODE_USER_NOT_FOUND, M.MSG_USER_NOT_FOUND, 404)
    return jsonify(dict(row))


@bp.route("/<int:user_id>", methods=["PUT"])
@require_api_key
def user_update(user_id):
    """PUT /admin/users/<id> -- partial update. Omitting a key leaves it
    unchanged; `email`/`mobile` sent as "" clear to null (v4.2). 400 if no
    valid fields; 404 USER_NOT_FOUND; 400 UNKNOWN_SERVER/UNKNOWN_CLIENT; 409 conflict."""
    data = parse_body()
    errors, cleaned = validate_user_payload(data, partial=True)
    if errors:
        return error(M.CODE_VALIDATION_FAILED, M.MSG_VALIDATION_FAILED, 400, details=errors)
    if not cleaned:
        return error(M.CODE_NO_FIELDS_TO_UPDATE, M.MSG_NO_FIELDS_TO_UPDATE, 400)

    with db() as conn:
        if not users_dal.user_id_exists(conn, user_id):
            return error(M.CODE_USER_NOT_FOUND, M.MSG_USER_NOT_FOUND, 404)

        if "server_id" in cleaned:
            if not servers_dal.server_id_exists(conn, cleaned["server_id"]):
                return error(M.CODE_UNKNOWN_SERVER, M.MSG_SERVER_ID_NOT_EXIST_TMPL.format(cleaned['server_id']), 400)

        if "client_name" in cleaned:
            if not clients_dal.client_name_exists(conn, cleaned["client_name"]):
                return error(M.CODE_UNKNOWN_CLIENT, M.MSG_UNKNOWN_CLIENT, 400, hint=M.MSG_UNKNOWN_CLIENT_HINT_TMPL.format(cleaned["client_name"]))

        try:
            row = users_dal.update_user(conn, user_id, cleaned)
        except sqlite3.IntegrityError as e:
            return error(M.CODE_CONFLICT, M.MSG_CONFLICT, 409, detail=str(e))

    log.info("User updated: id=%s fields=%s", user_id, list(cleaned.keys()))
    return jsonify(dict(row))


@bp.route("/<int:user_id>", methods=["DELETE"])
@require_api_key
def user_delete(user_id):
    """DELETE /admin/users/<id> -- remove ONE user row (leaves the client).
    404 USER_NOT_FOUND on miss. Full cascade is /admin/users/by-client/<name>."""
    with db() as conn:
        existing = users_dal.get_user_username(conn, user_id)
        if not existing:
            return error(M.CODE_USER_NOT_FOUND, M.MSG_USER_NOT_FOUND, 404)
        users_dal.delete_user_by_id(conn, user_id)

    log.info("User deleted: id=%s username=%s", user_id, existing['username'])
    return jsonify({"deleted": True, "id": user_id})


@bp.route("/<int:user_id>/disable", methods=["POST"])
@require_api_key
def user_disable(user_id):
    """POST /admin/users/<id>/disable -- set is_active=0; returns the updated row."""
    with db() as conn:
        if not users_dal.user_id_exists(conn, user_id):
            return error(M.CODE_USER_NOT_FOUND, M.MSG_USER_NOT_FOUND, 404)
        row = users_dal.set_user_active(conn, user_id, 0)
    log.info("User disabled: id=%s", user_id)
    return jsonify(dict(row))


@bp.route("/<int:user_id>/enable", methods=["POST"])
@require_api_key
def user_enable(user_id):
    """POST /admin/users/<id>/enable -- set is_active=1; returns the updated row."""
    with db() as conn:
        if not users_dal.user_id_exists(conn, user_id):
            return error(M.CODE_USER_NOT_FOUND, M.MSG_USER_NOT_FOUND, 404)
        row = users_dal.set_user_active(conn, user_id, 1)
    log.info("User enabled: id=%s", user_id)
    return jsonify(dict(row))


@bp.route("/by-client/<client_name>", methods=["DELETE"])
@require_api_key
def user_delete_by_client(client_name):
    """Cascade: delete all users for this client, AND remove the
    matching clients row (which holds the uKey). Single-call cleanup
    for MiracleCloud-Delete.ps1.

    Returns 200 even when nothing existed (v3.4 behavior preserved),
    with both `deleted` and `client_deleted` set to 0 in that case."""
    with db() as conn:
        result = users_bl.delete_by_client_cascade(conn, client_name)

    log.info("Users+client deleted by client: %s users=%d client_row=%d ukey=%s",
             result["canonical_name"], result["deleted_count"],
             result["client_deleted"], result["ukey"])
    return jsonify({
        "deleted":        result["deleted_count"],
        "usernames":      result["usernames"],
        "client_deleted": result["client_deleted"],
        "ukey":           result["ukey"],
        "client_name":    result["canonical_name"],
    })
