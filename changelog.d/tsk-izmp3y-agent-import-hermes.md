### Fixed
- Hermes agent import now validates tar bundles before handoff: rejects absolute paths and `..` traversal, drops symlinks and non-regular files (devices, FIFOs) with per-file notes in the import result.
- Imported profile's gateway is now explicitly configured via systemd drop-in (`Environment=HERMES_HOME=/root/.hermes/profiles/<slug>`) and restarted; import fails if the gateway does not serve the imported profile (verified via `hermes profile active`).
- Imported profile's model/provider config is rewritten to the taOS gateway (same values as a fresh deploy); original config backed up to `config.yaml.pre-import` inside the profile.
- Optional `pause_jobs: true` field on POST `/api/agents/import` pauses imported scheduled jobs (sets `enabled: false, state: "paused"` in `jobs.json`).
- Transfer notice written to agent memory and staged as first chat message: covers what moved, new container home, inaccessible host paths/localhost, rewritten model route, scheduled jobs list, dropped files list, missing credentials, and how to request access.
- Failed imports leave no half-record: agent record is marked `status: failed` with `failure_reason` instead of being removed; deploy failures also clean up cleanly.
