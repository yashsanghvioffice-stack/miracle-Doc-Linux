"""
Admin endpoints for the clients (tenant/uKey) registry:
    POST   /admin/clients
    GET    /admin/clients
    GET    /admin/clients/<id>
    GET    /admin/clients/by-name/<name>
    GET    /admin/clients/exists/<name>   -- ALWAYS 200, for PS Setup pre-flight
    PUT    /admin/clients/<id>
    DELETE /admin/clients/<id>            -- row only; full cascade is on /admin/users/by-client/<name>
"""

import sqlite3

from flask import Blueprint, jsonify

import messages as M
from config import CLIENT_NAME_RE
from logger import log

from auth import require_api_key, parse_body
from dal.connection import db
from dal import clients_dal, users_dal
from bl.clients_bl import (
    validate_client_create_payload,
    validate_client_update_payload,
)


bp = Blueprint("admin_clients", __name__, url_prefix="/admin/clients")


@bp.route("", methods=["POST"])
@require_api_key
def client_create():
    """Create a (client_name, ukey) row. PowerShell Setup calls this
    BEFORE creating any user."""
    data = parse_body()
    errors, cleaned = validate_client_create_payload(data)
    if errors:
        return jsonify({"status": "error", "code": M.CODE_VALIDATION_FAILED,
                        "message": errors[0]}), 400
    client_name = cleaned["client_name"]
    ukey        = cleaned["ukey"]

    try:
        with db() as conn:
            row = clients_dal.create_client(conn, client_name, ukey)
    except sqlite3.IntegrityError as e:
        # Surface which field clashed
        with db() as conn:
            by_name = clients_dal.get_client_brief_by_name(conn, client_name)
            by_ukey = clients_dal.get_client_brief_by_ukey(conn, ukey)
        if by_name:
            return jsonify({
                "status":               "error",
                "code":                 M.CODE_CLIENT_NAME_EXISTS,
                "message":              M.MSG_CLIENT_NAME_EXISTS,
                "existing_id":          by_name["id"],
                "existing_client_name": by_name["client_name"],
                "existing_ukey":        by_name["ukey"],
            }), 409
        if by_ukey:
            return jsonify({
                "status":               "error",
                "code":                 M.CODE_UKEY_IN_USE,
                "message":              M.MSG_UKEY_IN_USE,
                "existing_id":          by_ukey["id"],
                "existing_client_name": by_ukey["client_name"],
                "existing_ukey":        by_ukey["ukey"],
            }), 409
        return jsonify({"status": "error", "code": M.CODE_CONFLICT,
                        "message": M.MSG_CONFLICT, "detail": str(e)}), 409

    log.info("Client created: id=%s name=%s ukey=%s", row["id"], client_name, ukey)
    return jsonify(dict(row)), 201


@bp.route("", methods=["GET"])
@require_api_key
def client_list():
    with db() as conn:
        rows = clients_dal.list_clients_enriched(conn)
    return jsonify({"clients": [dict(r) for r in rows], "count": len(rows)})


@bp.route("/<int:client_id>", methods=["GET"])
@require_api_key
def client_get(client_id):
    with db() as conn:
        row = clients_dal.get_client_by_id(conn, client_id)
        if not row:
            return jsonify({"status": "error", "code": M.CODE_CLIENT_NOT_FOUND,
                            "message": M.MSG_CLIENT_NOT_FOUND}), 404
        users = users_dal.list_users_for_client(conn, row["client_name"])
    out = dict(row)
    out["users"] = [dict(u) for u in users]
    out["user_count"] = len(users)
    return jsonify(out)


@bp.route("/by-name/<client_name>", methods=["GET"])
@require_api_key
def client_by_name(client_name):
    if not CLIENT_NAME_RE.match(client_name or ""):
        return jsonify({"status": "error", "code": M.CODE_INVALID_CLIENT_NAME,
                        "message": M.MSG_INVALID_CLIENT_NAME_SHORT}), 400
    with db() as conn:
        row = clients_dal.get_client_by_name(conn, client_name)
        if not row:
            return jsonify({"status": "error", "code": M.CODE_CLIENT_NOT_FOUND,
                            "message": M.MSG_CLIENT_NOT_FOUND}), 404
        users = users_dal.list_users_for_client(conn, row["client_name"])
    out = dict(row)
    out["users"] = [dict(u) for u in users]
    out["user_count"] = len(users)
    return jsonify(out)


@bp.route("/exists/<client_name>", methods=["GET"])
@require_api_key
def client_exists(client_name):
    """Duplicate-check endpoint. ALWAYS returns 200. Used by
    MiracleCloud-Setup.ps1 pre-flight."""
    if not CLIENT_NAME_RE.match(client_name or ""):
        return jsonify({"exists": False, "status": "error",
                        "code": M.CODE_INVALID_CLIENT_NAME,
                        "message": M.MSG_INVALID_CLIENT_NAME_SHORT}), 200

    with db() as conn:
        row = clients_dal.get_client_brief_by_name(conn, client_name)
        if not row:
            return jsonify({"exists": False})
        user_count = clients_dal.count_users_for_client(conn, row["client_name"])
        # Most-common server for this client (informational)
        srv = clients_dal.most_common_server_for_client(conn, row["client_name"])

    out = {
        "exists":      True,
        "id":          row["id"],
        "client_name": row["client_name"],
        "ukey":        row["ukey"],
        "user_count":  user_count,
    }
    if srv:
        out["server_ip"]   = srv["server_ip"]
        out["server_name"] = srv["server_name"]
    return jsonify(out)


@bp.route("/<int:client_id>", methods=["PUT"])
@require_api_key
def client_update(client_id):
    data = parse_body()
    errors, cleaned = validate_client_update_payload(data)
    if errors:
        return jsonify({"status": "error", "code": M.CODE_VALIDATION_FAILED,
                        "message": errors[0]}), 400
    if not cleaned:
        return jsonify({"status": "error", "code": M.CODE_NO_FIELDS_TO_UPDATE,
                        "message": M.MSG_NO_FIELDS_TO_UPDATE}), 400

    with db() as conn:
        existing = clients_dal.get_client_by_id(conn, client_id)
        if not existing:
            return jsonify({"status": "error", "code": M.CODE_CLIENT_NOT_FOUND,
                            "message": M.MSG_CLIENT_NOT_FOUND}), 404

        try:
            # Cascade the new client_name into users (denormalized FK)
            if "client_name" in cleaned and cleaned["client_name"].lower() != existing["client_name"].lower():
                clients_dal.cascade_rename_in_users(
                    conn, existing["client_name"], cleaned["client_name"]
                )
            row = clients_dal.update_client(conn, client_id, cleaned)
        except sqlite3.IntegrityError as e:
            msg = str(e).lower()
            if "client_name" in msg:
                return jsonify({"status": "error", "code": M.CODE_CLIENT_NAME_EXISTS,
                            "message": M.MSG_CLIENT_NAME_EXISTS}), 409
            if "ukey" in msg:
                return jsonify({"status": "error", "code": M.CODE_UKEY_IN_USE,
                            "message": M.MSG_UKEY_IN_USE}), 409
            return jsonify({"status": "error", "code": M.CODE_CONFLICT,
                        "message": M.MSG_CONFLICT, "detail": str(e)}), 409

    log.info("Client updated: id=%s fields=%s", client_id, list(cleaned.keys()))
    return jsonify(dict(row))


@bp.route("/<int:client_id>", methods=["DELETE"])
@require_api_key
def client_delete(client_id):
    """Delete the clients row only. Does NOT touch users.
    Use DELETE /admin/users/by-client/<name> for full cascade."""
    with db() as conn:
        row = clients_dal.get_client_by_id(conn, client_id)
        if not row:
            return jsonify({"status": "error", "code": M.CODE_CLIENT_NOT_FOUND,
                            "message": M.MSG_CLIENT_NOT_FOUND}), 404
        clients_dal.delete_client_by_id(conn, client_id)

    log.info("Client deleted (row only): id=%s name=%s",
             client_id, row["client_name"])
    return jsonify({
        "deleted":     True,
        "id":          client_id,
        "client_name": row["client_name"],
        "ukey":        row["ukey"],
    })
