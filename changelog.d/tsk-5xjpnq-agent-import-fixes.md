### Fixed

- Agent import: read the source host from the source_host form field (with
  the "another machine" fallback) instead of a bundle manifest, because
  Hermes exports (NousResearch/hermes-cli @ v2026.8.31,
  hermes_cli/profiles.py:export_profile) carry one top-level profile
  directory and no manifest naming the source machine; the synchronous
  archive read was removed from the request path.
- Agent import bot findings: create the systemd drop-in directory before
  pushing the drop-in file and fail the import if that fails; parse the
  HERMES_HOME assignment and compare the full value instead of a substring
  match; treat a failed read of config.yaml, jobs.json or MEMORY.md as a
  read failure (fail the import, never overwrite imported memory) rather
  than a confirmed absence; pause imported jobs before the gateway is
  restarted, not after; and never write model.default: null when the import
  omits the model (use the shipped default).
- Agent import: add a test that the import result is a failure (not a
  silent success) when notice delivery fails.
