"""
Admin endpoints for server_master (TSplus host registry):
    POST   /admin/servers
    GET    /admin/servers
    GET    /admin/servers/<id>
    PUT    /admin/servers/<id>
    DELETE /admin/servers/<id>
"""

import sqlite3

from flask import Blueprint, jsonify

import messages as M
from logger import log

from auth import require_api_key, parse_body
from dal.connection import db
from dal import servers_dal, users_dal
from bl.servers_bl import validate_server_payload


bp = Blueprint("admin_servers", __name__, url_prefix="/admin/servers")


@bp.route("", methods=["POST"])
@require_api_key
def server_create():
    """POST /admin/servers -- register a TSplus host. Body: {server_name, server_ip}.
    Returns 201 with the new row; 409 SERVER_NAME_EXISTS / SERVER_IP_EXISTS on a
    duplicate name or IP. PS Setup's pre-flight auto-calls this for a new server."""
    data = parse_body()
    errors, cleaned = validate_server_payload(data, partial=False)
    if errors:
        return jsonify({"status": "error", "code": M.CODE_VALIDATION_FAILED,
                            "message": M.MSG_VALIDATION_FAILED, "details": errors}), 400

    try:
        with db() as conn:
            row = servers_dal.create_server(conn, cleaned["server_name"], cleaned["server_ip"])
    except sqlite3.IntegrityError as e:
        msg = str(e).lower()
        if "server_name" in msg:
            return jsonify({"status": "error", "code": M.CODE_SERVER_NAME_EXISTS,
                            "message": M.MSG_SERVER_NAME_EXISTS}), 409
        if "server_ip" in msg:
            return jsonify({"status": "error", "code": M.CODE_SERVER_IP_EXISTS,
                            "message": M.MSG_SERVER_IP_EXISTS}), 409
        return jsonify({"status": "error", "code": M.CODE_CONFLICT,
                        "message": M.MSG_CONFLICT, "detail": str(e)}), 409

    log.info("Server created: id=%s name=%s ip=%s",
             row["id"], cleaned['server_name'], cleaned['server_ip'])
    return jsonify(dict(row)), 201


@bp.route("", methods=["GET"])
@require_api_key
def server_list():
    """GET /admin/servers -- every registered server as {servers, count}."""
    with db() as conn:
        rows = servers_dal.list_servers(conn)
    return jsonify({"servers": [dict(r) for r in rows], "count": len(rows)})


@bp.route("/<int:server_id>", methods=["GET"])
@require_api_key
def server_get(server_id):
    """GET /admin/servers/<id> -- one server row; 404 SERVER_NOT_FOUND on miss."""
    with db() as conn:
        row = servers_dal.get_server_by_id(conn, server_id)
    if not row:
        return jsonify({"status": "error", "code": M.CODE_SERVER_NOT_FOUND,
                        "message": M.MSG_SERVER_NOT_FOUND}), 404
    return jsonify(dict(row))


@bp.route("/<int:server_id>", methods=["PUT"])
@require_api_key
def server_update(server_id):
    """PUT /admin/servers/<id> -- partial update of server_name/server_ip.
    400 VALIDATION_FAILED / NO_FIELDS_TO_UPDATE; 404 if missing; 409 on clash."""
    data = parse_body()
    errors, cleaned = validate_server_payload(data, partial=True)
    if errors:
        return jsonify({"status": "error", "code": M.CODE_VALIDATION_FAILED,
                            "message": M.MSG_VALIDATION_FAILED, "details": errors}), 400
    if not cleaned:
        return jsonify({"status": "error", "code": M.CODE_NO_FIELDS_TO_UPDATE,
                        "message": M.MSG_NO_FIELDS_TO_UPDATE}), 400

    with db() as conn:
        if not servers_dal.server_id_exists(conn, server_id):
            return jsonify({"status": "error", "code": M.CODE_SERVER_NOT_FOUND,
                        "message": M.MSG_SERVER_NOT_FOUND}), 404

        try:
            row = servers_dal.update_server(conn, server_id, cleaned)
        except sqlite3.IntegrityError as e:
            msg = str(e).lower()
            if "server_name" in msg:
                return jsonify({"status": "error", "code": M.CODE_SERVER_NAME_EXISTS,
                            "message": M.MSG_SERVER_NAME_EXISTS}), 409
            if "server_ip" in msg:
                return jsonify({"status": "error", "code": M.CODE_SERVER_IP_EXISTS,
                            "message": M.MSG_SERVER_IP_EXISTS}), 409
            return jsonify({"status": "error", "code": M.CODE_CONFLICT,
                        "message": M.MSG_CONFLICT, "detail": str(e)}), 409

    log.info("Server updated: id=%s fields=%s", server_id, list(cleaned.keys()))
    return jsonify(dict(row))


@bp.route("/<int:server_id>", methods=["DELETE"])
@require_api_key
def server_delete(server_id):
    """DELETE /admin/servers/<id> -- remove a server. Refuses with 409
    CANNOT_DELETE_SERVER_WITH_USERS (+ user_count) while any user references it."""
    with db() as conn:
        if not servers_dal.server_id_exists(conn, server_id):
            return jsonify({"status": "error", "code": M.CODE_SERVER_NOT_FOUND,
                        "message": M.MSG_SERVER_NOT_FOUND}), 404

        user_count = users_dal.count_users_for_server(conn, server_id)
        if user_count > 0:
            return jsonify({
                "status":     "error",
                "code":       M.CODE_CANNOT_DELETE_SERVER_WITH_USERS,
                "message":    M.MSG_CANNOT_DELETE_SERVER_WITH_USERS,
                "user_count": user_count,
                "hint":       M.MSG_DELETE_USERS_HINT,
            }), 409

        servers_dal.delete_server_by_id(conn, server_id)

    log.info("Server deleted: id=%s", server_id)
    return jsonify({"deleted": True, "id": server_id})
