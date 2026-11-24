### Added

- **Notification delivery controls (#206)** — per-source delivery style (banner / alert / none) and Notification Centre visibility in Settings → Notifications, enforced by the notification store: alert-style notifications publish as persistent toasts, style "none" stores silently with no toast or web-push, and centre-off sources are toast-only and never stored. New global Do Not Disturb schedule (`GET/PUT /api/notifications/dnd`) — while active, notifications are stored but not delivered live. The prefs endpoints now carry `style` and `show_centre` alongside `muted`.
