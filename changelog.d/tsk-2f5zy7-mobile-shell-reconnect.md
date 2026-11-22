### Fixed
- Mobile shell: app window no longer sticks on "taOS is restarting..." after controller recovers. Backend health probe now retries with capped 5s backoff indefinitely, re-probes immediately on `visibilitychange`/`online`, and triggers reload via `onUp` callback when backend returns. Home action (F13) now works while restarting banner is shown.
