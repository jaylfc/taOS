### Fixed

- Fixed DELETE /api/agents/registry/{canonical_id} route to be idempotent when revoking an already-revoked entry. Previously, it would raise an unhandled ValueError and return HTTP 500 instead of returning the existing record with 200 like the route docstring promised. The route now checks the record's status before attempting to revoke, returning the existing revoked record, and catches (ValueError, KeyError) exceptions like the _transition helper does. This matches the dev behavior and prevents client breakage.
