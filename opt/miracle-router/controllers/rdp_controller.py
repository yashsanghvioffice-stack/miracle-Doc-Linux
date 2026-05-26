"""
RDP file download endpoint:
    GET /rdp/download/<token>   -- single-use, time-limited

The token was issued during /login when preference=remote. Consuming
it serves a per-user .rdp file the browser hands to mstsc.exe.
Password is NOT in the file -- mstsc prompts the user.
"""

from flask import Blueprint, Response, make_response, request

from config import TOKEN_RE
from logger import log

from dal.connection import db
from bl import rdp_bl
from bl.rdp_bl import TokenOutcome


bp = Blueprint("rdp", __name__)


@bp.route("/rdp/download/<token>", methods=["GET"])
def rdp_download(token):
    """
    Single-use download of a per-user .rdp file.

      - Token must be 32 lowercase hex chars
      - Must exist in rdp_download_tokens
      - Must not have been used (used_at IS NULL)
      - Must be younger than RDP_TOKEN_TTL_SECONDS
      - Marks the token used atomically with the read

    Returns the .rdp content as application/x-rdp with a download
    Content-Disposition. mstsc.exe will then prompt the user for password.
    """
    if not TOKEN_RE.match(token or ''):
        log.warning("rdp_download: bad token format from %s: %r",
                    request.remote_addr, (token or '')[:80])
        return Response("Bad request", status=400)

    with db() as conn:
        outcome, row = rdp_bl.consume_token(conn, token)

    if outcome == TokenOutcome.NOT_FOUND:
        log.warning("rdp_download: unknown token from %s", request.remote_addr)
        return Response("Not found", status=404)

    if outcome == TokenOutcome.ALREADY_USED:
        log.warning("rdp_download: token already used (user=%s, used_at=%s) from %s",
                    row["username"], row["used_at"], request.remote_addr)
        return Response("Gone: token already used", status=410)

    if outcome == TokenOutcome.EXPIRED:
        log.warning("rdp_download: token expired (user=%s, age=%ss) from %s",
                    row["username"], row["age_seconds"], request.remote_addr)
        return Response("Gone: token expired", status=410)

    # Build the .rdp file content (BL: rdp_bl)
    rdp_content = rdp_bl.build_rdp_content(row["server_ip"], row["username"])

    log.info("RDP downloaded: user=%s server=%s from %s",
             row["username"], row["server_ip"], request.remote_addr)

    response = make_response(rdp_content)
    response.headers["Content-Type"]        = "application/x-rdp"
    response.headers["Content-Disposition"] = 'attachment; filename="miracle.rdp"'
    response.headers["Cache-Control"]       = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"]              = "no-cache"
    return response
