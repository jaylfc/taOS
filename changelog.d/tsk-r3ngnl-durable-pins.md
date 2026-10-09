### Changed
- Unregistering a cluster worker (`DELETE /api/cluster/workers/{name}`) now clears every agent pinned to that worker: the agent's `remote`, `placement_source`, `host`, `status`, and `placement_error` fields are reset, and the response returns an `unpinned_agents` list naming the affected agents. Marking a worker offline (revoke or block) does not unpin agents; only full deletion does.
