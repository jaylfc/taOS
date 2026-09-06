# Notifications Settings — Design Spec

## Overview

Add a macOS-style Notifications section to Settings that lets the user choose which notification categories they receive, and how they're delivered (toast, badge, sound, notification centre only).

Matches the pattern of macOS System Settings > Notifications: each app/source gets its own row with a master toggle plus delivery style controls.

## Problem

taOS fires notifications from many sources (cluster events, health alerts, agent deploys, knowledge pipeline, memory capture, etc.) with no user control. Users get every notification whether they want it or not. Can't silence noisy categories without disabling notifications entirely.

## Design

### Settings > Notifications section

New section in SettingsApp alongside "Memory", "Accessibility", etc. Opens to a list of **sources**, each with:

- **Allow notifications** — master on/off toggle
- **Delivery style** — segmented control:
  - **Banner** (default) — toast appears briefly, auto-dismisses
  - **Alert** — toast stays until dismissed
  - **None** — silent, only shows in Notification Centre
- **Show in Notification Centre** — on/off (for keeping a history even when banners are off)
- **Play sound** — on/off (future — speaker chirp when banner appears)

### Notification sources (categories)

Discovered from the codebase — each maps to a `source` field on `NotificationStore.addNotification()`:

| Source | Label | Default | Example |
|---|---|---|---|
| `cluster` | Cluster & Workers | Banner | "Worker fedora-host joined" |
| `health` | System Health | Alert | "RAM above 90%" |
| `agent` | Agents | Banner | "local-agent deployed" |
| `knowledge` | Knowledge Pipeline | Banner | "Ingestion complete: 120 chunks" |
| `memory` | Memory | None | "Captured new fact" |
| `webhook` | External Webhooks | Banner | "GitHub push on main" |
| `system` | System Updates | Alert | "Welcome to taOS" |
| `security` | Security Alerts | Alert | "Failed login attempt" |

### Storage

Persist user preferences as JSON on disk:

```
data/notification-prefs.json
{
  "cluster": { "enabled": true, "style": "banner", "show_centre": true, "sound": false },
  "health": { "enabled": true, "style": "alert", "show_centre": true, "sound": true },
  ...
}
```

Loaded at app startup, exposed via `GET /api/notifications/prefs`, updated via `PUT /api/notifications/prefs`.

### Backend enforcement

`NotificationStore.addNotification()` consults prefs at call time:

- If `enabled == false` → drop the notification entirely
- If `style == "none"` → add to store (for centre history) but don't emit toast event
- If `show_centre == false` → emit toast, skip store
- If `style == "alert"` → set `persistent: true` on the notification so frontend doesn't auto-dismiss

Sources the user has silenced should have their notifications dropped at the source so background polling doesn't waste RAM building notifications that get filtered client-side.

### Frontend changes

- New **NotificationsSection** in SettingsApp (same pattern as MemorySection, AccessibilitySection)
- Each source renders as an iOS 26 grouped list row with a disclosure indicator
- Tapping a source opens a sub-detail view (stacks on mobile via MobileSplitView) with the full per-source controls
- `NotificationToasts` checks `style` on each notification and respects "alert" vs "banner"
- `NotificationCentre` uses `show_centre` to decide whether to include an entry

### API

```
GET  /api/notifications/prefs           → { [source]: { enabled, style, show_centre, sound } }
PUT  /api/notifications/prefs           → replaces all
PATCH /api/notifications/prefs/{source} → updates one source
```

## Implementation Order

1. Backend: persistence (`NotificationPrefsStore`), wire into `NotificationStore.addNotification()`, API routes
2. Frontend: `NotificationsSection` in SettingsApp with the list + detail layout
3. Toast behaviour for `style == "alert"` (persistent until dismissed)
4. Sound support (optional — wire later when we add an audio file)

## Out of Scope

- Per-agent notification routing (agents can already post via `/api/notifications`; user can silence the whole `agent` category but not individual agents yet)
- Quiet hours / Do Not Disturb scheduling
- Focus modes
- Push notifications to a phone via PWA — separate spec

## References

- `tinyagentos/notifications.py` — existing NotificationStore
- `desktop/src/apps/SettingsApp.tsx` — section pattern to follow
- `desktop/src/components/NotificationToast.tsx` — toast renderer
- `desktop/src/components/NotificationCentre.tsx` — history view
- `desktop/src/stores/notification-store.ts` — frontend store
