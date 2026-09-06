# Schema Fields

- Identity: `handoff_id`, `mission_id`, `sequence`, `created_at`.
- Routing: `from_agent`, `to_agent`, `owner`, `state`.
- Authority: `path_scope`, `capabilities`, `permissions`, `limits`.
- Acceptance: `criteria`, `evidence`, `open_items`, `summary`.

Unknown fields fail so schema drift is visible. Input is limited to 1 MB and each repeated collection to 100 items.

