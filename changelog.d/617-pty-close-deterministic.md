### Fixed

- **Terminal PTY close is deterministic on disconnect (review on #4576)** — the shortcut terminal bridge now cancels the surviving direction, awaits its cancellation, and closes the PTY handle in a finally whose awaits are shielded from the handler's own cancellation, which the disconnect itself can deliver. An unshielded await in the cleanup could be aborted before the close ran, leaking the PTY; this failed `test_disconnect_with_quiet_shell_closes_pty` on Python 3.13 CI.
