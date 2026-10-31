### Security

- Removing a cloud agent from a project now also revokes its registry token. When the member's identity was minted by redeeming a kind `cloud_agent` invite on that project, `DELETE /api/projects/{id}/members/{member_id}` revokes the identity through the same registry transition and governance audit as `DELETE /api/agents/registry/{id}`, so it appears on `GET /api/agents/registry/revoked` and agent-JWT routes refuse its token. Agents from kind `agent` invites keep their identity as before. (tsk-xvc3xi)
