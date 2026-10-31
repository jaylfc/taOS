# What `GET /api/decisions/agent` returns (grant scoping)

<!-- Route module `tinyagentos/routes/decisions.py`, scope `decisions_write`. Lists the decisions THIS agent raised; the store layer enforces the `from_agent` binding, so there is no cross-agent leakage regardless of grants -->

## Grant shaping which decisions come back

- **Global (null-project) grant**: null-project decisions, plus granted projects
- **Exactly one granted scope**: those decisions, filtered in the store query
- **Two or more scopes**: one store query per scope, merged newest first

### Limit interaction

- Every scope pushes the project filter into the store query, and the merged list is capped at 500, so the limit applies AFTER scoping (issue #2194)
