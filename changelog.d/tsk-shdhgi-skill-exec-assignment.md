### Fixed

- Deployed agents presenting a registry JWT on `POST /api/skill-exec/{id}/call` can now only execute skills assigned to them; unassigned skills return 403 with "forbidden: skill not assigned to agent".
- Unknown-route handling for non-device Bearer tokens (404/401 split) is restored inside the non-device Bearer guard; a valid `taos_session` cookie with a device-prefixed Bearer on an unregistered path now returns 404 instead of 401.