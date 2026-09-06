### Fixed
- The Notifications settings panel is no longer a terminal dead end when the prefs fetch fails. It now renders a Retry control that re-issues the request through the same code path as the initial load, so a transient backend blip recovers without a full desktop reload.
