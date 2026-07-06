#!/usr/bin/env python3
"""
Miracle Cloud Gateway - Router API (v3.5) -- entry point.

After the Phase 0-6 refactor (2026-05), this file is just the
application wiring: it instantiates the Flask app, registers each
controller's Blueprint, runs the startup schema check, and hands
off to gunicorn (production) or werkzeug (dev).

Source-of-truth files for each concern:

    config.py / logger.py / messages.py
        env vars, regex patterns, RDP template, TSplus cookies,
        logging setup, every user-facing message + error code.

    dal/<resource>_dal.py
        Every SQL statement against the gateway DB. db() context
        manager + verify_schema() live in dal/connection.py.

    bl/<resource>_bl.py
        Business rules, validators, cross-DAL orchestration, the
        TSplus integration. Pure Python -- no Flask, no HTTP.

    controllers/<resource>_controller.py
        Flask routes (one Blueprint per resource). The HTTP layer:
        parse request, call BL, format response, log.

    auth.py
        require_api_key decorator + parse_body helper, shared by
        every admin controller.

ENV
    MIRACLE_API_KEY  Required. Shared secret for /admin/* routes.
    MIRACLE_DB_PATH  Optional. Defaults to /etc/miracle-registry/miracle.db
"""

from flask import Flask

from config import API_KEY
from dal.connection import verify_schema
from request_logging import install_request_logging

# ─── Controllers (Blueprints) ─────────────────────────────────────
from controllers.public_controller         import bp as public_bp
from controllers.rdp_controller            import bp as rdp_bp
from controllers.admin_servers_controller  import bp as admin_servers_bp
from controllers.admin_clients_controller  import bp as admin_clients_bp
from controllers.admin_users_controller    import bp as admin_users_bp
from controllers.admin_partners_controller import bp as admin_partners_bp
from controllers.admin_stats_controller    import bp as admin_stats_bp


# ─── App + blueprint registration ─────────────────────────────────

app = Flask(__name__)

app.register_blueprint(public_bp)
app.register_blueprint(rdp_bp)
app.register_blueprint(admin_servers_bp)
app.register_blueprint(admin_clients_bp)
app.register_blueprint(admin_users_bp)
app.register_blueprint(admin_partners_bp)
app.register_blueprint(admin_stats_bp)


# ─── Per-request audit logging (before/after/teardown hooks) ──────

install_request_logging(app)


# ─── Startup: schema check (will sys.exit on failure) ─────────────

verify_schema()


# ─── Dev entry point (gunicorn imports `app` directly) ────────────

if __name__ == "__main__":
    if not API_KEY:
        print("WARNING: MIRACLE_API_KEY env var not set. /admin/* will refuse all requests.")
    app.run(host="127.0.0.1", port=5001, debug=False)
