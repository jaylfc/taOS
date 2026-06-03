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
  updatePosition: (id: string, x: number, y: number) => void;
  updateSize: (id: string, w: number, h: number) => void;
  snapWindow: (id: string, snap: SnapPosition) => void;
  runningAppIds: () => string[];
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
      windows: s.windows.map((w) =>
        w.id === id
          ? { ...w, minimized: false, focused: true, zIndex: z }
          : { ...w, focused: false }
      ),
      nextZIndex: z + 1,
    }));
  },

  maximizeWindow(id) {
    set((s) => ({
      windows: s.windows.map((w) =>
        w.id === id ? { ...w, maximized: !w.maximized } : w
      ),
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
