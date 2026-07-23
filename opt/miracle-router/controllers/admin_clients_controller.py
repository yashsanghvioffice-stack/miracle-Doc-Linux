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

import config
import messages as M
from config import CLIENT_NAME_RE
from logger import log

from auth import require_api_key, parse_body
from dal.connection import db
from dal import clients_dal, users_dal, partners_dal
from bl.clients_bl import (
    validate_client_create_payload,
    validate_client_update_payload,
    apply_subscription_rules,
)


bp = Blueprint("admin_clients", __name__, url_prefix="/admin/clients")


def _with_display_fallback(row):
    """Single-row helper: convert a `clients` Row to dict and substitute
    display_name = client_name when the column is NULL (legacy rows).
    Used by endpoints that SELECT * from clients (which doesn't apply
    the COALESCE that the list/USER_SELECT queries do)."""
    d = dict(row)
    if d.get("display_name") in (None, ""):
        d["display_name"] = d.get("client_name")
    return d


@bp.route("", methods=["POST"])
@require_api_key
def client_create():
    """Create a (client_name, ukey, display_name) row. PowerShell Setup
    calls this BEFORE creating any user.

    `display_name` is optional in the body. When omitted or blank the
    column is persisted as the client_name so the UI always has a label.
    """
    data = parse_body()
    errors, cleaned = validate_client_create_payload(data)
    if errors:
        return jsonify({"status": "error", "code": M.CODE_VALIDATION_FAILED,
                        "message": errors[0]}), 400
    client_name  = cleaned["client_name"]
    ukey         = cleaned["ukey"]
    # Default the friendly label to the Customer ID when not provided.
    display_name = cleaned.get("display_name") or client_name

    # Default/auto-calc the client expiry (subscription_end). v4.1: start
    # is no longer stored on the client -- it lives on users.start_date.
    apply_subscription_rules(cleaned, is_create=True)

    # v4.1c: partner_id is mandatory on new setups (toggle via
    # MIRACLE_REQUIRE_PARTNER during the EXE/PS rollout window).
    partner_id = cleaned.get("partner_id")
    if partner_id is None and config.REQUIRE_PARTNER:
        return jsonify({"status": "error", "code": M.CODE_VALIDATION_FAILED,
                        "message": M.MSG_MISSING_PARTNER}), 400

    # A supplied partner_id must reference an existing partner.
    if partner_id is not None:
        with db() as conn:
            if not partners_dal.get_partner_by_id(conn, partner_id):
                return jsonify({"status": "error", "code": M.CODE_UNKNOWN_PARTNER,
                                "message": M.MSG_UNKNOWN_PARTNER_TMPL.format(partner_id)}), 400

    try:
        with db() as conn:
            row = clients_dal.create_client(
                conn, client_name, ukey, display_name,
                partner_id=partner_id,
                subscription_end=cleaned.get("subscription_end"),
                subscription_type=cleaned.get("subscription_type"),
                storage_gb=cleaned.get("storage_gb"),
                contact_email=cleaned.get("contact_email"),
                contact_mobile=cleaned.get("contact_mobile"),
            )
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

    log.info("Client created: id=%s name=%s display=%s ukey=%s",
             row["id"], client_name, display_name, ukey)
    return jsonify(_with_display_fallback(row)), 201


@bp.route("", methods=["GET"])
@require_api_key
def client_list():
    """GET /admin/clients -- all clients as {clients, count}, each enriched with
    partner_name + the admin (lowest-id) user's contact via LEFT JOIN."""
    with db() as conn:
        rows = clients_dal.list_clients_enriched(conn)
    return jsonify({"clients": [dict(r) for r in rows], "count": len(rows)})


@bp.route("/<int:client_id>", methods=["GET"])
@require_api_key
def client_get(client_id):
    """GET /admin/clients/<id> -- one client plus its `users` array and
    `user_count`; 404 CLIENT_NOT_FOUND on miss."""
    with db() as conn:
        row = clients_dal.get_client_by_id(conn, client_id)
        if not row:
            return jsonify({"status": "error", "code": M.CODE_CLIENT_NOT_FOUND,
                            "message": M.MSG_CLIENT_NOT_FOUND}), 404
        users = users_dal.list_users_for_client(conn, row["client_name"])
    out = _with_display_fallback(row)
    out["users"] = [dict(u) for u in users]
    out["user_count"] = len(users)
    return jsonify(out)


@bp.route("/by-name/<client_name>", methods=["GET"])
@require_api_key
def client_by_name(client_name):
    """GET /admin/clients/by-name/<name> -- same shape as client_get, looked up
    by Customer ID (case-insensitive). 400 on a malformed name; 404 on miss."""
    if not CLIENT_NAME_RE.match(client_name or ""):
        return jsonify({"status": "error", "code": M.CODE_INVALID_CLIENT_NAME,
                        "message": M.MSG_INVALID_CLIENT_NAME_SHORT}), 400
    with db() as conn:
        row = clients_dal.get_client_by_name(conn, client_name)
        if not row:
            return jsonify({"status": "error", "code": M.CODE_CLIENT_NOT_FOUND,
                            "message": M.MSG_CLIENT_NOT_FOUND}), 404
        users = users_dal.list_users_for_client(conn, row["client_name"])
    out = _with_display_fallback(row)
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
        row = clients_dal.get_client_by_name(conn, client_name)
        if not row:
            return jsonify({"exists": False})
        user_count = clients_dal.count_users_for_client(conn, row["client_name"])
        # Most-common server for this client (informational)
        srv = clients_dal.most_common_server_for_client(conn, row["client_name"])

    display_name = row["display_name"] if row["display_name"] else row["client_name"]
    out = {
        "exists":       True,
        "id":           row["id"],
        "client_name":  row["client_name"],
        "display_name": display_name,
        "ukey":         row["ukey"],
        "user_count":   user_count,
    }
    if srv:
        out["server_ip"]   = srv["server_ip"]
        out["server_name"] = srv["server_name"]
    return jsonify(out)


@bp.route("/<int:client_id>", methods=["PUT"])
@require_api_key
def client_update(client_id):
    """PUT /admin/clients/<id> -- partial update of any client field (display_name,
    ukey, partner_id, subscription_type/end, storage_gb, contact_email/mobile).
    Renaming client_name cascades into users.client_name. 400 no-fields /
    UNKNOWN_PARTNER; 404 CLIENT_NOT_FOUND; 409 CLIENT_NAME_EXISTS / UKEY_IN_USE."""
    data = parse_body()
    errors, cleaned = validate_client_update_payload(data)
    if errors:
        return jsonify({"status": "error", "code": M.CODE_VALIDATION_FAILED,
                        "message": errors[0]}), 400
    if not cleaned:
        return jsonify({"status": "error", "code": M.CODE_NO_FIELDS_TO_UPDATE,
                        "message": M.MSG_NO_FIELDS_TO_UPDATE}), 400

    # Phase 2: auto-calc subscription_end only when a new start is sent
    # without an explicit end (store-as-sent otherwise).
    apply_subscription_rules(cleaned, is_create=False)

    with db() as conn:
        existing = clients_dal.get_client_by_id(conn, client_id)
        if not existing:
            return jsonify({"status": "error", "code": M.CODE_CLIENT_NOT_FOUND,
                            "message": M.MSG_CLIENT_NOT_FOUND}), 404

        # Phase 2: a supplied partner_id must reference an existing partner.
        if cleaned.get("partner_id") is not None:
            if not partners_dal.get_partner_by_id(conn, cleaned["partner_id"]):
                return jsonify({"status": "error", "code": M.CODE_UNKNOWN_PARTNER,
                                "message": M.MSG_UNKNOWN_PARTNER_TMPL.format(cleaned["partner_id"])}), 400

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
    return jsonify(_with_display_fallback(row))


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
    display_name = row["display_name"] if row["display_name"] else row["client_name"]
    return jsonify({
        "deleted":      True,
        "id":           client_id,
        "client_name":  row["client_name"],
        "display_name": display_name,
        "ukey":         row["ukey"],
    })
