"""
Shared HTTP response shaping.

The ONE place the gateway's error envelope is constructed, so every endpoint
returns the identical `{status:"error", code, message, ...}` contract (see
docs/PROJECT_CONTEXT.md §5). Controllers call `error(...)` instead of hand-
building the dict + status tuple.
"""

from flask import jsonify


def error(code, message, status, **extra):
    """Build the standard error response tuple `(jsonify({...}), status)`.

    `code`/`message` are the machine code + human text; `status` is the HTTP
    status. `extra` adds any optional fields a specific error carries, e.g.
    `details=[...]`, `detail=str(e)`, `existing_id=...`, `user_count=...`,
    `hint=...`. Field order stays status/code/message first for readability.
    """
    body = {"status": "error", "code": code, "message": message}
    if extra:
        body.update(extra)
    return jsonify(body), status
