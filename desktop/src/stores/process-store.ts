import { create } from "zustand";

export type SnapPosition =
  | "left"
  | "right"
  | "top-left"
  | "top-right"
  | "bottom-left"
  | "bottom-right"
  | null;

export interface WindowState {
  id: string;
  appId: string;
  position: { x: number; y: number };
  size: { w: number; h: number };
  zIndex: number;
  minimized: boolean;
  maximized: boolean;
  snapped: SnapPosition;
  focused: boolean;
  closing?: boolean;
  props?: Record<string, unknown>;
  launchNonce: number;
}

interface ProcessStore {
  windows: WindowState[];
  nextZIndex: number;

  openWindow: (appId: string, defaultSize: { w: number; h: number }, props?: Record<string, unknown>) => string;
  closeWindow: (id: string) => void;
  removeWindow: (id: string) => void;
  focusWindow: (id: string) => void;
  minimizeWindow: (id: string) => void;
  restoreWindow: (id: string) => void;
  maximizeWindow: (id: string) => void;
  recenterWindow: (id: string) => void;
  updatePosition: (id: string, x: number, y: number) => void;
  updateSize: (id: string, w: number, h: number) => void;
  updateBounds: (id: string, x: number, y: number, w: number, h: number) => void;
  snapWindow: (id: string, snap: SnapPosition) => void;
  runningAppIds: () => string[];
}

// Compute on-screen-safe bounds for a window. Used when restoring or
// un-maximizing so a window that drifted off-screen (or is larger than the
// current desktop) comes back at a usable size, recentered if it is no longer
// reachable. The desktop area sits below the 32px top bar and above the ~84px
// dock reservation, matching Window.tsx. Idempotent for windows already
// comfortably on-screen. Pass a far-off position to force a recenter.
function safeBounds(
  position: { x: number; y: number },
  size: { w: number; h: number },
): { position: { x: number; y: number }; size: { w: number; h: number } } {
  const topBarH = 32;
  const dockH = 84;
  const margin = 16;
  const vw = typeof window !== "undefined" ? window.innerWidth : 1280;
  const vh = typeof window !== "undefined" ? window.innerHeight : 800;
  const deskW = vw;
  const deskH = vh - topBarH - dockH;

  // Never larger than the desktop (minus margins); never below a usable minimum.
  const w = Math.max(300, Math.min(size.w, deskW - margin * 2));
  const h = Math.max(200, Math.min(size.h, deskH - margin * 2));

  let { x, y } = position;
  // "Reachable" means enough of the title bar is on the desktop to grab it.
  const GRAB = 80;
  const reachable = x <= deskW - GRAB && x + w >= GRAB && y >= 0 && y <= deskH - topBarH;
  if (!reachable) {
    x = Math.round((deskW - w) / 2);
    y = Math.round((deskH - h) / 2);
  }
  // Final clamp keeps even a reachable-but-oversized window fully in view.
  x = Math.max(margin, Math.min(x, Math.max(margin, deskW - w - margin)));
  y = Math.max(0, Math.min(y, Math.max(0, deskH - h - margin)));
  return { position: { x, y }, size: { w, h } };
}

let idCounter = 0;

export const useProcessStore = create<ProcessStore>((set, get) => ({
  windows: [],
  nextZIndex: 1,

  openWindow(appId, defaultSize, props) {
    const existing = get().windows.find((w) => w.appId === appId);
    if (existing) {
      if (props) {
        set((s) => ({
          windows: s.windows.map((w) =>
            w.id === existing.id
              ? { ...w, props: { ...w.props, ...props }, launchNonce: w.launchNonce + 1 }
              : w
          ),
        }));
      }
      get().restoreWindow(existing.id);
      return existing.id;
    }
    const id = `win-${++idCounter}`;
    const z = get().nextZIndex;
    const offset = (get().windows.length % 8) * 30;
    const win: WindowState = {
      id,
      appId,
      position: { x: 80 + offset, y: 60 + offset },
      size: defaultSize,
      zIndex: z,
      minimized: false,
      maximized: false,
      snapped: null,
      focused: true,
      props,
      launchNonce: 0,
    };
    set((s) => ({
      windows: s.windows.map((w) => ({ ...w, focused: false })).concat(win),
      nextZIndex: z + 1,
    }));
    return id;
  },

  closeWindow(id) {
    // Mark the window as closing so the Window component can run its
    // close animation. The window stays mounted until the animation
    // completes and calls removeWindow(id).
    set((s) => ({
      windows: s.windows.map((w) =>
        w.id === id ? { ...w, closing: true } : w
      ),
    }));
  },

  removeWindow(id) {
    set((s) => ({ windows: s.windows.filter((w) => w.id !== id) }));
  },

  focusWindow(id) {
    const z = get().nextZIndex;
    set((s) => ({
      windows: s.windows.map((w) => ({
        ...w,
        focused: w.id === id,
        zIndex: w.id === id ? z : w.zIndex,
      })),
      nextZIndex: z + 1,
    }));
  },

  minimizeWindow(id) {
    set((s) => ({
      windows: s.windows.map((w) =>
        w.id === id ? { ...w, minimized: true, focused: false } : w
      ),
    }));
  },

  restoreWindow(id) {
    const z = get().nextZIndex;
    set((s) => ({
      windows: s.windows.map((w) => {
        if (w.id !== id) return { ...w, focused: false };
        // Showing a window again: if it drifted off-screen while hidden, pull
        // it back into view. A maximized window keeps its stored bounds for
        // when it is later un-maximized.
        const safe = w.maximized ? {} : safeBounds(w.position, w.size);
        return { ...w, ...safe, minimized: false, focused: true, zIndex: z };
      }),
      nextZIndex: z + 1,
    }));
  },

  maximizeWindow(id) {
    set((s) => ({
      windows: s.windows.map((w) => {
        if (w.id !== id) return w;
        if (!w.maximized) {
          // Maximizing implies showing the window, even from a minimized state.
          return { ...w, maximized: true, minimized: false };
        }
        // Un-maximizing: make sure the restored bounds are on-screen.
        const safe = safeBounds(w.position, w.size);
        return { ...w, ...safe, maximized: false };
      }),
    }));
  },

  recenterWindow(id) {
    const z = get().nextZIndex;
    set((s) => ({
      windows: s.windows.map((w) => {
        if (w.id !== id) return { ...w, focused: false };
        // Force a recenter (far-off position guarantees safeBounds recenters),
        // and ensure the window is shown and not maximized so the user can see
        // and move it. The recovery path for a window lost off-screen.
        const safe = safeBounds({ x: -1e6, y: -1e6 }, w.size);
        return { ...w, ...safe, minimized: false, maximized: false, focused: true, zIndex: z };
      }),
      nextZIndex: z + 1,
    }));
  },

  updatePosition(id, x, y) {
    set((s) => ({
      windows: s.windows.map((w) =>
        w.id === id ? { ...w, position: { x, y } } : w
      ),
    }));
  },

  updateSize(id, w, h) {
    set((s) => ({
      windows: s.windows.map((win) =>
        win.id === id ? { ...win, size: { w, h } } : win
      ),
    }));
  },

  // Position and size in ONE update. A resize from a top/left edge moves the
  // window's x/y as well as its w/h; committing them separately renders one
  // frame with the new size but the old position, which reads as a jump. This
  // applies both atomically.
  updateBounds(id, x, y, w, h) {
    set((s) => ({
      windows: s.windows.map((win) =>
        win.id === id ? { ...win, position: { x, y }, size: { w, h } } : win
      ),
    }));
  },

  snapWindow(id, snap) {
    set((s) => ({
      windows: s.windows.map((w) =>
        w.id === id ? { ...w, snapped: snap } : w
      ),
    }));
  },

  runningAppIds() {
    return get().windows.map((w) => w.appId);
  },
}));
