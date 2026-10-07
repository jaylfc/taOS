### Fixed

- The `proof_status` classification logic in `POST /api/agents/auth-requests` is now held in a single `_classify_proof` helper, removing the duplicated block. The `project_create` path no longer carries its own unreachable "none" and "rejected" branches: `_resolve_agent_identity(strict=True)` answers 401 for an unproven caller before any proof classification runs, so the path always records `proof_status="accepted"` when it reaches the store.
