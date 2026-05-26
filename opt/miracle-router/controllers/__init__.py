"""
Miracle Cloud Gateway -- HTTP controllers (Flask Blueprints).

Each module here defines a `bp` Blueprint registered by router.py.
Controllers parse the HTTP request, call into BL (validation,
orchestration, integrations), then format the HTTP response.

Hard rules:
    - NO raw SQL in this layer (use DAL via BL or directly for plain reads)
    - NO Flask `app` references (use the module-level `bp` Blueprint)
    - Logging happens HERE (not in BL) -- the controller knows what
      operation and HTTP context the log line belongs to

Modules:
    public_controller         -- /health, /login, /logout
    rdp_controller            -- /rdp/download/<token>
    admin_servers_controller  -- /admin/servers/*
    admin_clients_controller  -- /admin/clients/*
    admin_users_controller    -- /admin/users/*, /admin/users/by-client/<name>
    admin_stats_controller    -- /admin/stats
"""
