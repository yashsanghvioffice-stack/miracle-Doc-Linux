"""
Miracle Cloud Gateway -- Data Access Layer (DAL).

Every SQL query against the gateway DB lives in this package. Higher
layers (BL, controllers) call DAL functions and never write SQL
themselves. This boundary is enforced by convention, not the language --
keep it tight.

Modules:
    connection      -- db() context manager + verify_schema() startup check
    users_dal       -- queries against the `users` table
    clients_dal     -- queries against the `clients` table
    servers_dal     -- queries against `server_master`
    rdp_tokens_dal  -- queries against `rdp_download_tokens`
    request_log_dal -- INSERT-only writes to `request_log` (per-request audit)
    stats_dal       -- aggregate queries for /health and /admin/stats
"""
