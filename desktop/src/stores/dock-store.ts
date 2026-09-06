import { create } from "zustand";

const DEFAULT_PINNED = ["messages", "agents", "files", "store", "settings"];

export type DockIconSize = "small" | "medium" | "large";
export type DockPosition = "bottom" | "left";

interface DockStore {
  pinned: string[];
  iconSize: DockIconSize;
  position: DockPosition;
  pin: (appId: string) => void;
  unpin: (appId: string) => void;
  reorder: (appIds: string[]) => void;
  setIconSize: (size: DockIconSize) => void;
  setPosition: (position: DockPosition) => void;
}

export const useDockStore = create<DockStore>((set, get) => ({
  pinned: DEFAULT_PINNED,
  iconSize: "medium",
  position: "bottom",

  pin(appId) {
    if (get().pinned.includes(appId)) return;
    set((s) => ({ pinned: [...s.pinned, appId] }));
  },

  unpin(appId) {
    set((s) => ({ pinned: s.pinned.filter((id) => id !== appId) }));
  },

  reorder(appIds) {
    set({ pinned: appIds });
  },

  setIconSize(size) {
    set({ iconSize: size });
  },

  setPosition(position) {
    set({ position });
  },
}));
