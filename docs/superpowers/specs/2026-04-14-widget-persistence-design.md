# Widget Persistence — Design Spec

## Problem

Widgets on the desktop (Clock, Agent Status, System Stats, Weather, Quick Notes) only persist in browser localStorage. On a fresh browser, different machine, or after clearing state, removed widgets reappear and layout resets. Doesn't work for the "follow me between devices" session model we already have for windows.

## Design

Move widget state to the server. Identical shape to the existing session persistence for windows — owned by the signed-in user, synced on mutation, loaded on app boot.

### Data shape

```
{
  "widgets": [
    { "id": "default-clock", "type": "clock", "x": 0, "y": 0, "w": 4, "h": 3 },
    ...
  ],
  "show_widgets": true
}
```

### Storage

Extend the existing session persistence store (`tinyagentos/session_store.py` or similar). Add a widget slot per user. Schema:

```
data/sessions/{user}/widgets.json
```

### API

```
GET  /api/widgets/layout           → full layout
PUT  /api/widgets/layout           → replace entirely (includes empty list when user removes all)
```

Batch writes — the frontend debounces mutations (drag/resize/add/remove) by 500ms before sending.

### Frontend changes

`useWidgetStore` (Zustand) currently hits localStorage. Refactor to:

1. On app boot: fetch `/api/widgets/layout`, populate store with server data (empty array OK — means the user has removed all widgets).
2. On every mutation (`addWidget`, `removeWidget`, `updateLayout`, `toggleWidgets`): debounced PUT to `/api/widgets/layout`.
3. localStorage becomes a fallback cache for offline / pre-auth state only.

Default widgets (Clock, Agent Status) are seeded server-side on first login, not in the frontend. That way removing them actually sticks.

### Migration

On boot, if server returns 404 / no layout file exists, check localStorage for a legacy layout. If present, POST it to the server as the initial layout and clear localStorage. One-shot migration; avoids user losing their existing layout.

## Out of Scope

- Per-device widget layouts (share across all devices for now — matches window session behaviour)
- Widget marketplace / custom widgets (separate spec)
- Widget themes

## Related

- `desktop/src/stores/widget-store.ts` — current in-memory store
- `desktop/src/components/WidgetLayer.tsx` — render + mutations
- `tinyagentos/routes/session.py` — window session endpoints, the pattern to copy
