### Fixed
- Fix assign-agent binding incorrectly treating expired or live global grants (e.g. `decisions_read` with `project_id=None`) as deferred project grants. The binding logic in `add_agent_to_project` now restricts deferred-grant expiry inheritance to project-scoped grants only (those in `_PROJECT_SCOPES`). This resolves:
  - BUG A: an expired global grant no longer blocks project binding with a 400 error
  - BUG B: a live global grant no longer caps the new project grant's expiry