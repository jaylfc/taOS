### Fixed
- Server refuses to archive a bell for a still-pending request (agent_scope_requests or auth_requests); it is marked read and stays listed.
- Approve or deny of the request archives the bell via the existing _retire_* helpers.
- clearAll now archives every notification except srv- rows whose source is agent_scope_requests or auth_requests (local rows archived as before).