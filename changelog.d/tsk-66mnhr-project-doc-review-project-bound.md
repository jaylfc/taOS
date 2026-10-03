### Fixed

- `project_doc_review` is now a project-bound scope. Approving it through the auth-request or scope-request flow without an operator-picked project is refused with 400 instead of silently minting a global (`project_id = None`) grant that `check_agent_scope_for_project` can never match. `project_notes` already was; both are now asserted by tests.