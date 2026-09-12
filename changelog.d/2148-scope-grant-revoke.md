### Security

- Agent scope grants can now be revoked. `AgentGrantsStore` gained
  `revoke_grant(canonical_id, scope, project_id=...)` and
  `revoke_all_for_project(canonical_id, project_id)`, and
  `POST /api/projects/{id}/members/revoke-agent` is the owner-or-admin route over
  them (an omitted/empty `scopes` list revokes every grant the agent holds on
  that project). A revoked scope is refused at check time on the agent's next
  request, the agent's other scopes and its grants on other projects are
  untouched, and the revoke is audit-logged as `member.grants_revoked` on the
  project activity feed. Previously the only removal was revoking the whole
  identity (`agent_registry_store.revoke`), so least privilege was unachievable
  (#2148).

### Fixed

- `POST /api/projects/{id}/members/assign-agent` no longer reports a revocation
  it did not perform. Its `scopes` list is now the agent's complete scope set
  for the project: a grant on that project which the body does not name is
  revoked, so `scopes: []` really removes the access instead of answering
  `{"granted_scopes": []}` while the registry grant stayed live. The response
  reports `revoked_scopes` and the read-back `active_scopes`, and fails with 500
  if the reconciliation does not take effect rather than claiming success
  (#2148).
