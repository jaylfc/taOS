### Fixed

- Agents app: project-grant rows from the admin grant feed now mark expired
  grants with an `expired` tag and hide their revoke button, and time-boxed
  rows show `expires_at` in the row title. The admin `?canonical_id=` feed is
  deliberately unfiltered, so expired rows were rendering as live grants.
- Agents app: a rejected per-scope revoke now surfaces the server's `detail`
  message (FastAPI `HTTPException`) instead of collapsing every 400/403/404
  into a bare `HTTP <status>`.
- Agents app: the in-flight revoke state is keyed on `<project>:<scope>`, so
  revoking a scope on one project no longer disables the same-named scope on
  every other project.
