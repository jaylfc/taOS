### Fixed

- desktop-control holds ONE persistent virtual keyboard per session (created once with the seat's real layout, kept alive), never a per-command one
- taOS sway config/patch set ensures new clients always receive the active physical keyboard's keymap
