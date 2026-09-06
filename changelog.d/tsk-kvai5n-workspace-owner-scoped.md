### Security

- `/data/workspace` agent paths are now ownership-checked: only the agent owner or an admin may read files; unauthenticated requests still return 401 and path traversal is rejected.
