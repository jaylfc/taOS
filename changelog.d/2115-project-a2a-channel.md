### Fixed

- Projects: creating a project now always creates its A2A channel. Previously only `POST /api/projects` did; the agent skill (`create_project`) and the `project_create` decision approval path created the project row without a channel, leaving members with no project thread (#2115).

- Projects: membership paths now sync the A2A channel too, not just project creation. Approving a grant of only canvas scopes (`canvas_read`/`canvas_write`, no `project_tasks`) or assigning an agent with only canvas scopes wrote the member row but never called `ensure_a2a_channel`, and accepting a cross-instance collab invite via the peer inbox added the human member with no channel sync at all — a project whose creation-time ensure had failed (it is best-effort) was left with members but no project thread. All membership-writing paths now ensure the channel; the call remains idempotent (#2115).
