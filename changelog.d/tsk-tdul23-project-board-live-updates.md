### Fixed

- Projects board: live updates now subscribe to the per-project event stream (/api/projects/{id}/events) instead of /api/os/events, which never carries task.* events (task.created|updated|claimed|released|closed|reopened|parked|quarantined|unquarantined). useBoardLive already subscribes to that per-project stream via the projectsApi SSE helper, applies task events, refreshes on task changes, and closes the stream when the project changes or the board unmounts.
