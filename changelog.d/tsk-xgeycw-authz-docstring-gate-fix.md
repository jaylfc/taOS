### Fixed
- Restored docstrings on 16 mutating agent endpoints that were displaced by the authz insert in PR #3120, so OpenAPI descriptions are no longer blank.
- Hardened the authz gate's body-authz check to ignore commented-out calls and require the call to be awaited as a statement, closing a potential fail-open path.