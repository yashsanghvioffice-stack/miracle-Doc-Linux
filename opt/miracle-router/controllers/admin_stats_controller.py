"""
Admin endpoint for aggregate gateway stats:
    GET /admin/stats
"""

from flask import Blueprint, jsonify

from auth import require_api_key
from dal.connection import db
from dal import stats_dal


bp = Blueprint("admin_stats", __name__)


@bp.route("/admin/stats", methods=["GET"])
@require_api_key
def admin_stats():
    with db() as conn:
        snap = stats_dal.admin_stats_snapshot(conn)
    snap["per_server"] = [dict(r) for r in snap["per_server"]]
    return jsonify(snap)
