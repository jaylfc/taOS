import { useEffect } from "react";
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
 * This is the agent-tool side of the deep-navigation API (#836) and complements
 * `taos:open-app`.
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

const TOP = 36; // below the 32px top bar
const DOCK = 88; // above the dock

function workArea() {
  const W = window.innerWidth;
  const H = window.innerHeight;
  return { W, H, x: 8, y: TOP, w: W - 16, h: H - TOP - DOCK };
}

function getLayout(): DesktopLayout {
  const { windows } = useProcessStore.getState();
  return {
    screen: { width: window.innerWidth, height: window.innerHeight, ratio: +(window.innerWidth / window.innerHeight).toFixed(3) },
    windows: windows.map((w) => ({
      id: w.id,
      appId: w.appId,
      x: w.position.x,
      y: w.position.y,
      w: w.size.w,
      h: w.size.h,
      minimized: w.minimized,
      maximized: w.maximized,
      snapped: w.snapped,
      focused: w.focused,
      zIndex: w.zIndex,
    })),
  };
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
    s.snapWindow(id, null);
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
      const id = s.openWindow(app.id, size, op.props);
      if (op.x != null && op.y != null) s.updatePosition(id, op.x, op.y);
      return id;
    }
    case "move": {
      const id = resolveId(op);
      if (id && op.x != null && op.y != null) s.updatePosition(id, op.x, op.y);
      return;
    }
    case "resize": {
      const id = resolveId(op);
      if (id && op.w != null && op.h != null) s.updateSize(id, op.w, op.h);
      return;
    }
    case "maximize": {
      const id = resolveId(op);
      if (id) s.maximizeWindow(id);
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

export function useDesktopControl(): void {
  useEffect(() => {
    const onWindow = (e: Event) => {
      const detail = (e as CustomEvent).detail as WindowOp | undefined;
      if (detail?.action) run(detail);
    };
    window.addEventListener("taos:window", onWindow);
    window.taosDesktop = { getLayout, run };
    return () => {
      window.removeEventListener("taos:window", onWindow);
      delete window.taosDesktop;
    };
  }, []);
}
