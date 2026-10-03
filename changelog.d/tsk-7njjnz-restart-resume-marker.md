### Fixed

- Restart resume now preserves user-set paused flags from the Agents app,
  disk quota, and failure handler by tracking a `paused_by_restart` marker
  that is set only when a restart prepare returns 200. Only agents carrying
  that marker are resumed or have their paused flag cleared on retry window
  expiry. A 200 prepare also deletes any stale controller note for that
  agent, so a later `/resume` POST is not blocked by an old note. Lifecycle
  calls are skipped for agents with no recorded port instead of guessing
  port 8080.
- A 200 prepare no longer deletes the agent-framework's own `resume_note.json`:
  controller-side fallback notes are identified by `_is_controller_note` and
  removed only when they are actually stale. Agents whose frameworks answered
  `/prepare-for-shutdown` keep their notes through restart.
- A restart prepare no longer adopts an agent that is already paused by the
  user (no `paused_by_restart` marker). Such agents stay paused and are
  excluded from the restart resume pass.
