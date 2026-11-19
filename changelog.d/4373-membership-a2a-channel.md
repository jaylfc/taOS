### Fixed

- Projects: membership paths now sync the A2A channel too, not just project creation. Approving a grant of only canvas scopes (`canvas_read`/`canvas_write`, no `project_tasks`) or assigning an agent with only canvas scopes wrote the member row but never called `ensure_a2a_channel`, and accepting a cross-instance collab invite via the peer inbox added the human member with no channel sync at all — a project whose creation-time ensure had failed (it is best-effort) was left with members but no project thread. All membership-writing paths now ensure the channel; the call remains idempotent (#2115).

- Projects: the A2A channel sync no longer drops members it cannot find in `config.agents`. A native member backed by the agent registry now stays in the channel under its registry handle, and an accepted human collaborator stays under their contact id, instead of being silently removed on the next sync (#2115).
