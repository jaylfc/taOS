### Added

- **tmux-absent terminal fallback (#617, follow-up to #4571)** — when the agent container image has no tmux (current images do not ship it yet; tracked in #4574), the shortcut terminal now opens with the plain pre-#617 spawn and a one-line "this terminal won't persist" notice instead of spawning a tmux session that cannot start. The terminal never fails to open because persistence is unavailable; the list/restart/delete routes degrade honestly (empty list / 404) when tmux is absent.

### Fixed

- **`exec_in_container` shares one timeout budget** — the caller's timeout now covers the incus project lookup as well as the exec itself, instead of the lookup silently running on the 120s default ahead of a short-timeout call (the terminal routes use 15s). Found in review on #4571.
