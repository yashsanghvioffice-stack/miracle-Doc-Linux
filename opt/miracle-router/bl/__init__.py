"""
Miracle Cloud Gateway -- Business Logic (BL) layer.

Resource rules, orchestration across DAL calls, and external-system
integrations (TSplus). BL takes plain args, returns plain data
(primitives, Rows, small typed results). It does NOT know about Flask,
HTTP status codes, or JSON.

Modules:
    auth_bl       -- login bind + is_active rule (the "find authenticated
                     user" workflow)
    tsplus_bl     -- TSplus integration: hb.exe HTTP call + cookie set
    rdp_bl        -- RDP token lifecycle + build_rdp_content
    users_bl      -- (Phase 5) user CRUD orchestration + validation
    clients_bl    -- (Phase 5) client CRUD orchestration
    servers_bl    -- (Phase 5) server CRUD orchestration + validation
"""
