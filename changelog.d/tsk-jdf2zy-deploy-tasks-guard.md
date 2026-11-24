### Fixed

- Drop the stale `deploy_tasks` entry when an agent is archived, purged, or restored so a reused name no longer reports the previous lifecycle's deploy status.
- Guard `purge_archived` to only remove the `deploy_tasks` entry when no live agent holds the archived name, preventing a redeployed agent's deploy status from being dropped.
