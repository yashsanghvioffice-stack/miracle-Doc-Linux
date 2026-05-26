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
    data = parse_body()
    errors, cleaned = validate_user_payload(data, partial=False)
    if errors:
        return jsonify({"status": "error", "code": M.CODE_VALIDATION_FAILED,
                        "message": M.MSG_VALIDATION_FAILED, "details": errors}), 400

    with db() as conn:
        if not servers_dal.server_id_exists(conn, cleaned["server_id"]):
            return jsonify({"status": "error", "code": M.CODE_UNKNOWN_SERVER,
                            "message": M.MSG_SERVER_ID_NOT_EXIST_TMPL.format(cleaned['server_id'])}), 400

        # client_name MUST exist in clients table (uKey precondition)
        if not clients_dal.client_name_exists(conn, cleaned["client_name"]):
            return jsonify({
                "status":  "error",
                "code":    M.CODE_UNKNOWN_CLIENT,
                "message": M.MSG_UNKNOWN_CLIENT,
                "hint":    M.MSG_UNKNOWN_CLIENT_HINT_TMPL.format(cleaned["client_name"]),
            }), 400

        try:
            row = users_dal.create_user(
                conn,
                cleaned["username"],
                cleaned["client_name"],
                cleaned["email"],
                cleaned["mobile"],
                cleaned["server_id"],
                cleaned.get("is_active", 1),
            )
        except sqlite3.IntegrityError as e:
            return jsonify({"status": "error", "code": M.CODE_USERNAME_EXISTS,
                            "message": M.MSG_USERNAME_EXISTS, "detail": str(e)}), 409

    log.info("User created: id=%s username=%s", row["id"], cleaned['username'])
    return jsonify(dict(row)), 201


@bp.route("", methods=["GET"])
@require_api_key
def user_list():
    active_only = request.args.get("active_only", "").lower() in ("1", "true", "yes")
    client      = request.args.get("client_name")
    server_id   = request.args.get("server_id")

    with db() as conn:
        rows = users_dal.list_users(conn, active_only=active_only,
                                    client_name=client, server_id=server_id)

    return jsonify({"users": [dict(r) for r in rows], "count": len(rows)})


@bp.route("/<int:user_id>", methods=["GET"])
@require_api_key
def user_get(user_id):
    with db() as conn:
        row = users_dal.get_user_by_id(conn, user_id)
    if not row:
        return jsonify({"status": "error", "code": M.CODE_USER_NOT_FOUND,
                        "message": M.MSG_USER_NOT_FOUND}), 404
    return jsonify(dict(row))


@bp.route("/<int:user_id>", methods=["PUT"])
@require_api_key
def user_update(user_id):
    data = parse_body()
    errors, cleaned = validate_user_payload(data, partial=True)
    if errors:
        return jsonify({"status": "error", "code": M.CODE_VALIDATION_FAILED,
                        "message": M.MSG_VALIDATION_FAILED, "details": errors}), 400
    if not cleaned:
        return jsonify({"status": "error", "code": M.CODE_NO_FIELDS_TO_UPDATE,
                        "message": M.MSG_NO_FIELDS_TO_UPDATE}), 400

    with db() as conn:
        if not users_dal.user_id_exists(conn, user_id):
            return jsonify({"status": "error", "code": M.CODE_USER_NOT_FOUND,
                        "message": M.MSG_USER_NOT_FOUND}), 404

        if "server_id" in cleaned:
            if not servers_dal.server_id_exists(conn, cleaned["server_id"]):
                return jsonify({"status": "error", "code": M.CODE_UNKNOWN_SERVER,
                            "message": M.MSG_SERVER_ID_NOT_EXIST_TMPL.format(cleaned['server_id'])}), 400

        if "client_name" in cleaned:
            if not clients_dal.client_name_exists(conn, cleaned["client_name"]):
                return jsonify({
                    "status":  "error",
                    "code":    M.CODE_UNKNOWN_CLIENT,
                    "message": M.MSG_UNKNOWN_CLIENT,
                    "hint":    M.MSG_UNKNOWN_CLIENT_HINT_TMPL.format(cleaned["client_name"]),
                }), 400

        try:
            row = users_dal.update_user(conn, user_id, cleaned)
        except sqlite3.IntegrityError as e:
            return jsonify({"status": "error", "code": M.CODE_CONFLICT,
                            "message": M.MSG_CONFLICT, "detail": str(e)}), 409

    log.info("User updated: id=%s fields=%s", user_id, list(cleaned.keys()))
    return jsonify(dict(row))


@bp.route("/<int:user_id>", methods=["DELETE"])
@require_api_key
def user_delete(user_id):
    with db() as conn:
        existing = users_dal.get_user_username(conn, user_id)
        if not existing:
            return jsonify({"status": "error", "code": M.CODE_USER_NOT_FOUND,
                        "message": M.MSG_USER_NOT_FOUND}), 404
        users_dal.delete_user_by_id(conn, user_id)

    log.info("User deleted: id=%s username=%s", user_id, existing['username'])
    return jsonify({"deleted": True, "id": user_id})


@bp.route("/<int:user_id>/disable", methods=["POST"])
@require_api_key
def user_disable(user_id):
    with db() as conn:
        if not users_dal.user_id_exists(conn, user_id):
            return jsonify({"status": "error", "code": M.CODE_USER_NOT_FOUND,
                        "message": M.MSG_USER_NOT_FOUND}), 404
        row = users_dal.set_user_active(conn, user_id, 0)
    log.info("User disabled: id=%s", user_id)
    return jsonify(dict(row))


@bp.route("/<int:user_id>/enable", methods=["POST"])
@require_api_key
def user_enable(user_id):
    with db() as conn:
        if not users_dal.user_id_exists(conn, user_id):
            return jsonify({"status": "error", "code": M.CODE_USER_NOT_FOUND,
                        "message": M.MSG_USER_NOT_FOUND}), 404
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
