# Desktop control API

Programmatic control of the taOS desktop: query the layout and drive window
lifecycle / placement without clicking. This is the agent-tool side of the
deep-navigation API (issue #836) and the foundation for agent-arranged layouts
and deterministic screenshots / tests.

Implemented by `desktop/src/hooks/use-desktop-control.ts`, mounted once in
`Desktop.tsx`. It exposes a global and a CustomEvent.

## Global: `window.taosDesktop`

```js
// Read the current layout: screen size + ratio and every window's bounds/state.
window.taosDesktop.getLayout();
// => {
//   screen: { width, height, ratio },
//   windows: [{ id, appId, x, y, w, h, minimized, maximized, snapped, focused, zIndex }]
// }

// Run a single window operation (returns the new window id for "open").
window.taosDesktop.run({ action: "open", appId: "chat" });
```

## Event: `taos:window`

Same payload as `run`, for fire-and-forget control from anywhere:

```js
window.dispatchEvent(new CustomEvent("taos:window", { detail: { action: "arrange", preset: "tile-3" } }));
```

## Operations

| action | fields | effect |
| --- | --- | --- |
| `open` | `appId`, optional `x`,`y`,`w`,`h`,`props` | open (or focus) an app, optionally placed/sized |
| `close` / `focus` / `minimize` / `restore` / `maximize` | target | window lifecycle |
| `move` | target, `x`,`y` | reposition |
| `resize` | target, `w`,`h` | resize |
| `snap` | target, `snap` (left/right/top-left/top-right/bottom-left/bottom-right/null) | snap-tile |
| `arrange` | `preset` (`tile-2` / `tile-3` / `center` / `cascade`) | arrange all open windows |

**Targeting precedence:** explicit `windowId`, else the first window for `appId`,
else the focused / topmost window.

The presets respect the work area (below the 32px top bar, above the dock).

## Screenshots / tests

Drive a deterministic layout from Playwright instead of clicking:

```js
await page.evaluate(() => {
  window.taosDesktop.run({ action: "open", appId: "chat" });
  window.taosDesktop.run({ action: "open", appId: "projects" });
  window.taosDesktop.run({ action: "open", appId: "store" });
  window.taosDesktop.run({ action: "arrange", preset: "tile-3" });
});
const layout = await page.evaluate(() => window.taosDesktop.getLayout());
```

## Controller → browser transport (backend command channel)

`window.taosDesktop` and the `taos:window` / `taos:open-app` events run in the
browser. To let the **controller** (and through it, the taOS agent) drive a
specific user's desktop, the backend streams commands to the browser over SSE.

- **Backend broker:** `tinyagentos/desktop_control/broker.py` —
  `DesktopCommandBroker`, one channel per `user_id`, **no replay** (a command is a
  one-shot side effect; replaying buffered commands to a reconnecting desktop
  would re-open closed apps).
- **Routes:** `tinyagentos/routes/desktop_control.py`
  - `GET /api/desktop/stream` — SSE; the desktop subscribes (scoped to the
    caller's `user_id`), receives each command as `data: {kind, payload, ts}`.
  - `POST /api/desktop/command` — body `{kind, payload}`; emits to the caller's
    own desktop(s). Returns `{delivered: N}` (0 = no desktop connected). Privileged:
    only the agent runtime / authed server-side callers push here, never arbitrary
    clients; a user only ever drives their own desktop.
- **Browser receiver:** `desktop/src/hooks/use-desktop-command-stream.ts`
  (mounted in `Desktop.tsx`) subscribes to the stream and re-dispatches each
  command to the existing receivers:
  - `{ kind: "open-app", payload: { app, props } }` → `taos:open-app`
  - `{ kind: "window",   payload: WindowOp }`       → `taos:window`

So a command pushed server-side lands on the same `useDeepNavigation` /
`useDesktopControl` handlers a local caller would hit — no new app logic.

```bash
# Open the Projects app on the calling user's desktop:
curl -X POST /api/desktop/command \
  -H 'Content-Type: application/json' \
  -d '{"kind":"open-app","payload":{"app":"projects"}}'
```

## Follow-up

Built (this PR): the controller→browser transport above. **Next:** the agent MCP
tools (`open_app`, `arrange_windows`, plus the data tools that wrap existing
project/canvas/image routes) that call `POST /api/desktop/command`, and the
matching agent-manual entry — they land together so the manual only advertises
capabilities the agent can actually invoke. `getLayout` over the channel (a
browser→backend read round-trip) is deferred until screen-aware arrange needs it.
