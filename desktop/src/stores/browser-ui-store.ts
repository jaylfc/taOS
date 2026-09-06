/**
 * browser-ui-store — persistent UI state for the BrowserApp shell.
 *
 * Kept separate from browser-store (which owns window/tab/session data) so
 * sidebar collapse state does not pollute the data model.
 *
 * The collapsed state persists across window opens within the same session via
 * zustand in-memory state. We intentionally avoid localStorage here — the
 * desktop restores session state through useSessionPersistence, and layout
 * prefs don't belong in session snapshots.
 */
import { create } from "zustand";

interface BrowserUiState {
  /** Whether the left sessions/tabs sidebar is collapsed to icon-only mode. */
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
}

export const useBrowserUiStore = create<BrowserUiState>((set) => ({
  sidebarCollapsed: false,
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
}));
