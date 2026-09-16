### Added
- Gate the inlined `litellm` proxy extra mirrors against a snapshot so dependabot
  cannot widen a sibling ceiling past the litellm 1.94.2 set the list mirrors
  (fixes #3083 ceiling drift on gunicorn/mcp/rich/websockets and future siblings).
