import { useEffect, useMemo, type Dispatch, type SetStateAction } from "react";
import { useProcessStore, type SnapPosition } from "@/stores/process-store";
import { resolveApp } from "@/registry/app-registry";

/**
 * Programmatic desktop control for the taOS agent (and for deterministic
 * screenshots / tests). Exposes `window.taosDesktop` and a `taos:window`
 * CustomEvent so a window's lifecycle and layout can be driven without clicking:
 *
 *   window.taosDesktop.getLayout()            // screen size + every window's bounds/state
 *   window.taosDesktop.run({ action, ... })   // drive one window op
 *   window.dispatchEvent(new CustomEvent("taos:window", { detail: { action, ... } }))
 *
 * Actions: open, close, focus, minimize, restore, maximize, move, resize, snap,
 * arrange (preset: tile-2 | tile-3 | center | cascade). Targeting precedence:
 * explicit windowId, else first window for appId, else the focused/topmost one.
 * `close` is the one exception: given an appId with no windowId, it closes
 * every open window for that app (an app can have more than one via forceNew).
 * The CustomEvent also accepts `op`/`app` as aliases for `action`/`appId` (the
 * sibling `taos:open-app` event uses `app`, so callers reasonably guess it here
 * too); an event with neither `action` nor `op` is rejected with a console.warn
 * so contract drift is visible instead of silently doing nothing.
 * This is the agent-tool side of the deep-navigation API (#836) and complements
 * `taos:open-app`.
 *
 * The phone layout never mounts <Desktop/>, so it registers the same receiver
 * through `useMobileControl`: there an op picks the one app on screen instead
 * of placing a window (see runMobile).
 */

export interface DesktopLayout {
  screen: { width: number; height: number; ratio: number };
  windows: Array<{
    id: string;
    appId: string;
    x: number;
    y: number;
    w: number;
    h: number;
    minimized: boolean;
    maximized: boolean;
    snapped: SnapPosition;
    focused: boolean;
    zIndex: number;
  }>;
}

export interface WindowOp {
  action:
    | "open"
    | "close"
    | "focus"
    | "minimize"
    | "restore"
    | "maximize"
    | "move"
    | "resize"
    | "snap"
    | "arrange";
  windowId?: string;
  appId?: string;
  props?: Record<string, unknown>;
  x?: number;
  y?: number;
  w?: number;
  h?: number;
  snap?: SnapPosition;
  preset?: "tile-2" | "tile-3" | "center" | "cascade";
}

// The valid WindowOp actions, as a runtime allowlist for the event receiver
// (the type union alone cannot guard an untrusted CustomEvent payload).
const WINDOW_ACTIONS = new Set<WindowOp["action"]>([
  "open", "close", "focus", "minimize", "restore",
  "maximize", "move", "resize", "snap", "arrange",
]);

const TOP = 36; // below the 32px top bar
const DOCK = 88; // above the dock

function workArea() {
  const W = window.innerWidth;
  const H = window.innerHeight;
  return { W, H, x: 8, y: TOP, w: W - 16, h: H - TOP - DOCK };
}

function getLayout(): DesktopLayout {
  const { windows } = useProcessStore.getState();
  const area = workArea();
  return {
    screen: { width: window.innerWidth, height: window.innerHeight, ratio: +(window.innerWidth / window.innerHeight).toFixed(3) },
    windows: windows.map((w) => {
      // A maximized window fills the work area regardless of its stored bounds;
      // report the rendered geometry so callers see where it actually is.
      const m = w.maximized;
      return {
        id: w.id,
        appId: w.appId,
        x: m ? area.x : w.position.x,
        y: m ? area.y : w.position.y,
        w: m ? area.w : w.size.w,
        h: m ? area.h : w.size.h,
        minimized: w.minimized,
        maximized: w.maximized,
        snapped: w.snapped,
        focused: w.focused,
        zIndex: w.zIndex,
      };
    }),
  };
}

// Clear maximized / snapped state so an explicit move/resize actually applies
// (the Window derives its rendered geometry from those, ignoring stored bounds).
function freePlacement(id: string) {
  const s = useProcessStore.getState();
  const win = s.windows.find((w) => w.id === id);
  if (!win) return;
  if (win.snapped) s.snapWindow(id, null);
  if (win.maximized) s.maximizeWindow(id); // maximizeWindow toggles; this un-maximizes
}

function resolveId(op: WindowOp): string | undefined {
  const { windows } = useProcessStore.getState();
  if (op.windowId) return op.windowId;
  if (op.appId) return windows.find((w) => w.appId === op.appId)?.id;
  return [...windows].sort((a, b) => b.zIndex - a.zIndex)[0]?.id;
}

function arrange(preset: WindowOp["preset"]) {
  const s = useProcessStore.getState();
  const wins = [...s.windows].filter((w) => !w.minimized).sort((a, b) => a.zIndex - b.zIndex);
  if (wins.length === 0) return;
  const a = workArea();
  const place = (id: string, x: number, y: number, w: number, h: number) => {
    freePlacement(id);
    s.updatePosition(id, Math.round(x), Math.round(y));
    s.updateSize(id, Math.round(w), Math.round(h));
  };
  if (preset === "tile-2") {
    const half = (a.w - 8) / 2;
    place(wins[0]!.id, a.x, a.y, half, a.h);
    if (wins[1]) place(wins[1].id, a.x + half + 8, a.y, half, a.h);
  } else if (preset === "tile-3") {
    const third = (a.w - 16) / 3;
    wins.slice(0, 3).forEach((w, i) => place(w.id, a.x + i * (third + 8), a.y, third, a.h));
  } else if (preset === "center") {
    const w = Math.min(a.w, 1100);
    const h = Math.min(a.h, 720);
    wins.forEach((win) => place(win.id, a.x + (a.w - w) / 2, a.y + (a.h - h) / 2, w, h));
  } else if (preset === "cascade") {
    const w = Math.min(a.w * 0.62, 900);
    const h = Math.min(a.h * 0.7, 620);
    wins.forEach((win, i) => place(win.id, a.x + 40 + i * 36, a.y + 20 + i * 32, w, h));
  }
}

function run(op: WindowOp): string | void {
  const s = useProcessStore.getState();
  switch (op.action) {
    case "open": {
      const app = op.appId ? resolveApp(op.appId) : undefined;
      if (!app) return;
      const size = op.w != null && op.h != null ? { w: op.w, h: op.h } : app.defaultSize;
      // In-page even on the handset: the agent gets this id back and drives
      // the window's geometry with it.
      const id = s.openWindow(app.id, size, op.props, { inPage: true });
      if (op.x != null && op.y != null) s.updatePosition(id, op.x, op.y);
      return id;
    }
    case "move": {
      const id = resolveId(op);
      if (id && op.x != null && op.y != null) {
        freePlacement(id);
        s.updatePosition(id, op.x, op.y);
      }
      return;
    }
    case "resize": {
      const id = resolveId(op);
      if (id && op.w != null && op.h != null) {
        freePlacement(id);
        s.updateSize(id, op.w, op.h);
      }
      return;
    }
    case "maximize": {
      const id = resolveId(op);
      const win = id ? s.windows.find((w) => w.id === id) : undefined;
      if (win && !win.maximized) s.maximizeWindow(win.id); // always maximizes, never toggles off
      return;
    }
    case "minimize": {
      const id = resolveId(op);
      if (id) s.minimizeWindow(id);
      return;
    }
    case "restore": {
      const id = resolveId(op);
      if (id) s.restoreWindow(id);
      return;
    }
    case "focus": {
      const id = resolveId(op);
      if (id) s.focusWindow(id);
      return;
    }
    case "close": {
      // appId with no explicit windowId closes every window for that app,
      // not just the first match -- an app can have several (forceNew).
      if (!op.windowId && op.appId) {
        s.windows.filter((w) => w.appId === op.appId).forEach((w) => s.closeWindow(w.id));
        return;
      }
      const id = resolveId(op);
      if (id) s.closeWindow(id);
      return;
    }
    case "snap": {
      const id = resolveId(op);
      if (id) s.snapWindow(id, op.snap ?? null);
      return;
    }
    case "arrange":
      arrange(op.preset);
      return;
  }
}

declare global {
  interface Window {
    taosDesktop?: { getLayout: () => DesktopLayout; run: (op: WindowOp) => string | void };
  }
}

/**
 * The phone layout's view of App state: which app is on screen. A functional
 * update, so a close can clear the shown app without the receiver tracking it.
 */
export interface MobileControl {
  setActive: Dispatch<SetStateAction<string | null>>;
}

function runMobile(op: WindowOp, m: MobileControl): string | void {
  const s = useProcessStore.getState();
  switch (op.action) {
    case "open": {
      const app = op.appId ? resolveApp(op.appId) : undefined;
      if (!app) return;
      const id = s.openWindow(app.id, app.defaultSize, op.props, { inPage: true });
      m.setActive(id);
      return id;
    }
    case "focus":
    case "restore": {
      const id = resolveId(op);
      if (!id || !s.windows.some((w) => w.id === id)) return;
      if (op.action === "restore") s.restoreWindow(id);
      s.focusWindow(id);
      m.setActive(id);
      return;
    }
    case "minimize": {
      const id = resolveId(op);
      if (!id) return;
      s.minimizeWindow(id);
      m.setActive((prev) => (prev === id ? null : prev));
      return;
    }
    case "close": {
      // removeWindow, not closeWindow: closeWindow only marks a window closing
      // for the desktop Window's close animation, which this layout never
      // mounts, so the app would stay in the switcher.
      const one = resolveId(op);
      const ids =
        !op.windowId && op.appId
          ? s.windows.filter((w) => w.appId === op.appId).map((w) => w.id)
          : one
            ? [one]
            : [];
      ids.forEach((id) => s.removeWindow(id));
      m.setActive((prev) => (prev && ids.includes(prev) ? null : prev));
      return;
    }
    default:
      // move, resize, maximize, snap, arrange: every app here is full screen.
      return;
  }
}

function useWindowReceiver(runOp: ((op: WindowOp) => string | void) | null): void {
  useEffect(() => {
    if (!runOp) return;
    const onWindow = (e: Event) => {
      // `op`/`app` are accepted as aliases for `action`/`appId` (see module
      // docstring) since they mirror the sibling taos:open-app payload shape.
      const raw = (e as CustomEvent).detail as Record<string, unknown> | undefined;
      const candidate = raw?.action ?? raw?.op;
      // Allowlist the action so an unknown string (e.g. op: "shutdown") is
      // rejected loudly here rather than falling through run()'s switch as a
      // silent no-op, which is the very behaviour this receiver fixes.
      if (typeof candidate !== "string" || !WINDOW_ACTIONS.has(candidate as WindowOp["action"])) {
        console.warn("taos:window ignored: missing or unknown action/op", raw);
        return;
      }
      const action = candidate as WindowOp["action"];
      // appId must be a string; a non-string app silently fails the lookup.
      const rawAppId = raw?.appId ?? raw?.app;
      const appId = typeof rawAppId === "string" ? rawAppId : undefined;
      runOp({ ...(raw as Partial<WindowOp>), action, appId });
    };
    window.addEventListener("taos:window", onWindow);
    window.taosDesktop = { getLayout, run: runOp };
    return () => {
      window.removeEventListener("taos:window", onWindow);
      delete window.taosDesktop;
    };
  }, [runOp]);
}

export function useDesktopControl(): void {
  useWindowReceiver(run);
}

/**
 * The receiver for the phone layout. `enabled` is false in desktop mode, where
 * <Desktop/> registers its own and two would delete each other's on unmount.
 */
export function useMobileControl({ setActive }: MobileControl, enabled: boolean): void {
  const runOp = useMemo(
    () => (enabled ? (op: WindowOp) => runMobile(op, { setActive }) : null),
    [enabled, setActive],
  );
  useWindowReceiver(runOp);
}
