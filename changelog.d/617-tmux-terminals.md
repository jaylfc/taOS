### Added

- **Persistent agent terminals (#617, backend slice)** — terminal/TUI shortcut windows now run inside a tmux session (`taos-term-<agent>-<kind>-<idx>`) in the agent's container: closing the window detaches and reopening reattaches instead of killing the shell. New lifecycle routes `GET /api/agents/{name}/terminals`, `POST /api/agents/{name}/terminals/{session}/restart` and `DELETE /api/agents/{name}/terminals/{session}` back the settings screen's running-terminals list. The settings UI itself, tmux in the agent container images, and orphan TTL/cap values remain open on #617.
