"""
Admin endpoints for the partners master list:
    POST   /admin/partners
    GET    /admin/partners            (?active_only=true)
    GET    /admin/partners/<id>
    PUT    /admin/partners/<id>
    DELETE /admin/partners/<id>        -- SOFT delete when referenced by
                                          any client (is_active=0);
                                          HARD delete when unreferenced.

Partner is the account-level attribute chosen at Setup. Individual
users do NOT carry a partner -- see /admin/clients Phase-2 changes
for how clients bind to a partner.
"""

import sqlite3

from flask import Blueprint, jsonify, request
from responses import error

import messages as M
from logger import log

from auth import require_api_key, parse_body
from dal.connection import db
from dal import partners_dal
from bl.partners_bl import (
    validate_partner_create_payload,
    validate_partner_update_payload,
)


bp = Blueprint("admin_partners", __name__, url_prefix="/admin/partners")


@bp.route("", methods=["POST"])
@require_api_key
def partner_create():
    """Create a partner. Only `name` is required."""
    data = parse_body()
    errors, cleaned = validate_partner_create_payload(data)
    if errors:
        return error(M.CODE_VALIDATION_FAILED, errors[0], 400)

    name  = cleaned["name"]
    email = cleaned.get("email")
    phone = cleaned.get("phone")

    try:
        with db() as conn:
            row = partners_dal.create_partner(conn, name, email, phone)
    except sqlite3.IntegrityError:
        # Only unique constraint on partners is `name`
        with db() as conn:
            existing = partners_dal.get_partner_by_name(conn, name)
        return error(M.CODE_PARTNER_NAME_EXISTS, M.MSG_PARTNER_NAME_EXISTS, 409, existing_id=existing["id"] if existing else None)

    log.info("Partner created: id=%s name=%s", row["id"], name)
    return jsonify(dict(row)), 201


@bp.route("", methods=["GET"])
@require_api_key
def partner_list():
    """List partners. `?active_only=true` filters to is_active=1 (used
    by the desktop dropdown so operators only see selectable partners)."""
    active_only = request.args.get("active_only", "").lower() in ("1", "true", "yes")
    with db() as conn:
        rows = partners_dal.list_partners(conn, active_only=active_only)
    return jsonify({"partners": [dict(r) for r in rows], "count": len(rows)})


@bp.route("/<int:partner_id>", methods=["GET"])
@require_api_key
def partner_get(partner_id):
    """GET /admin/partners/<id> -- one partner row; 404 PARTNER_NOT_FOUND on miss."""
    with db() as conn:
        row = partners_dal.get_partner_by_id(conn, partner_id)
    if not row:
        return error(M.CODE_PARTNER_NOT_FOUND, M.MSG_PARTNER_NOT_FOUND, 404)
    return jsonify(dict(row))


@bp.route("/<int:partner_id>", methods=["PUT"])
@require_api_key
def partner_update(partner_id):
    """PUT /admin/partners/<id> -- partial update of {name, email, phone, is_active}.
    `email` cannot be cleared (mandatory, v4.1c). 400 no-fields; 404 PARTNER_NOT_FOUND;
    409 PARTNER_NAME_EXISTS on a duplicate name."""
    data = parse_body()
    errors, cleaned = validate_partner_update_payload(data)
    if errors:
        return error(M.CODE_VALIDATION_FAILED, errors[0], 400)
    if not cleaned:
        return error(M.CODE_NO_FIELDS_TO_UPDATE, M.MSG_NO_FIELDS_TO_UPDATE, 400)

    with db() as conn:
        if not partners_dal.get_partner_by_id(conn, partner_id):
            return error(M.CODE_PARTNER_NOT_FOUND, M.MSG_PARTNER_NOT_FOUND, 404)

        try:
            row = partners_dal.update_partner(conn, partner_id, cleaned)
        except sqlite3.IntegrityError:
            return error(M.CODE_PARTNER_NAME_EXISTS, M.MSG_PARTNER_NAME_EXISTS, 409)

    log.info("Partner updated: id=%s fields=%s", partner_id, list(cleaned.keys()))
    return jsonify(dict(row))


@bp.route("/<int:partner_id>", methods=["DELETE"])
@require_api_key
def partner_delete(partner_id):
    """Soft delete (is_active=0) when the partner is referenced by any
    client -- keeps historical joins intact. Hard delete when unused.

    Response `deleted_kind` is 'soft' or 'hard' so the desktop app can
    surface the difference to the operator."""
    with db() as conn:
        row = partners_dal.get_partner_by_id(conn, partner_id)
        if not row:
            return error(M.CODE_PARTNER_NOT_FOUND, M.MSG_PARTNER_NOT_FOUND, 404)

        n = partners_dal.count_clients_for_partner(conn, partner_id)
        if n > 0:
            partners_dal.soft_delete_partner(conn, partner_id)
            kind = "soft"
        else:
            partners_dal.delete_partner_by_id(conn, partner_id)
            kind = "hard"

    log.info("Partner %s-deleted: id=%s name=%s referenced_by=%d",
             kind, partner_id, row["name"], n)
    return jsonify({
        "deleted":       True,
        "deleted_kind":  kind,
        "id":            partner_id,
        "name":          row["name"],
        "referenced_by": n,
    })
