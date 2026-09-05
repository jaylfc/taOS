### Fixed
- A legacy `notification-archive` dock pin now survives a session restore and opens the Notifications Archive tab. Restoring the dock rewrote the pin to `notifications`, which threw away the Archive destination before the dock, the Ctrl+N shortcuts or the app could read it, and the dock auto-save then wrote the stripped id back to the server, so one reload lost the pin for good.
