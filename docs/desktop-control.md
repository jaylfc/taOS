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

## Follow-up

The controller-side agent tool (`desktop_get_layout` / `desktop_arrange`) that
lets the taOS agent call this over the agent bridge, and the matching agent
manual entry, are a separate change (see [[agent-desktop-control]] in memory).
