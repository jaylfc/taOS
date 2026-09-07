### Security

- Fixed a path traversal in `POST /api/import/upload` and `POST /api/import/embed`: a client-supplied filename such as an absolute path or `../` escaped the upload directory, letting any authenticated user write or read files the server process can reach, including the auth store. Names must now be a plain basename that resolves inside the upload directory, and `agent_name` is validated. Reported by EQSTLab (GHSA-rwrp-hfc4-qg2w).
