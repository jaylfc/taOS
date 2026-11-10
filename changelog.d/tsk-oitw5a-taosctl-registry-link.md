### Added

- New `taosctl agent-registry links` verb to list unlinked config agents with candidate canonical IDs (admin-only). This mirrors the existing registry-link routes in the API.
- New `taosctl agent-registry link <agent_name> <canonical_id>` verb to link a config agent to a canonical ID via POST `/api/agents/{agent_name}/registry-link` (admin-only). This mirrors the existing registry-link routes in the API.

### Documentation

- Updated `docs/agent-coordination.md` to include the new registry-link routes under the existing registry-feeds bullet point.
