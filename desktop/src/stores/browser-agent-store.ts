import { create } from "zustand";

const MIN_PANEL_WIDTH = 240;
const MAX_PANEL_WIDTH = 480;
const DEFAULT_PANEL_WIDTH = 280;
const WATCHING_DECAY_MS = 3000;

export interface AgentPanelState {
  isOpen: boolean;
  activeAgentId: string | null;
  width: number;
}

export interface BrowserAgentState {
  /** Per-(windowId, tabId) panel state. Key: `${windowId}:${tabId}`. */
  panels: Record<string, AgentPanelState>;

  /** Per-(windowId, tabId, agentId) timestamp of the last WS event received.
   * Used by AgentPresencePill to compute the "watching" pulse state.
   * Key: `${windowId}:${tabId}:${agentId}`. */
  lastEventAt: Record<string, number>;

  openPanel(windowId: string, tabId: string, agentId: string): void;
  closePanel(windowId: string, tabId: string): void;
  togglePanel(windowId: string, tabId: string, agentId: string): void;
  setActiveAgent(windowId: string, tabId: string, agentId: string): void;
  setPanelWidth(windowId: string, tabId: string, width: number): void;
  bumpEventAt(windowId: string, tabId: string, agentId: string, at?: number): void;

  /** True if `lastEventAt` for this key is within WATCHING_DECAY_MS of now. */
  isWatching(windowId: string, tabId: string, agentId: string): boolean;
}

export const useBrowserAgentStore = create<BrowserAgentState>((set, get) => ({
  panels: {},
  lastEventAt: {},

  openPanel(windowId, tabId, agentId) {
    set((state) => {
      const key = `${windowId}:${tabId}`;
      const existing = state.panels[key];
      return {
        panels: {
          ...state.panels,
          [key]: {
            isOpen: true,
            activeAgentId: agentId,
            width: existing?.width ?? DEFAULT_PANEL_WIDTH,
          },
        },
      };
    });
  },

  closePanel(windowId, tabId) {
    set((state) => {
      const key = `${windowId}:${tabId}`;
      const existing = state.panels[key];
      if (!existing) return state;
      return {
        panels: {
          ...state.panels,
          [key]: { ...existing, isOpen: false },
        },
      };
    });
  },

  togglePanel(windowId, tabId, agentId) {
    const key = `${windowId}:${tabId}`;
    const existing = get().panels[key];
    if (existing?.isOpen) {
      get().closePanel(windowId, tabId);
    } else {
      get().openPanel(windowId, tabId, agentId);
    }
  },

  setActiveAgent(windowId, tabId, agentId) {
    set((state) => {
      const key = `${windowId}:${tabId}`;
      const existing = state.panels[key];
      if (!existing) return state;
      return {
        panels: {
          ...state.panels,
          [key]: { ...existing, activeAgentId: agentId },
        },
      };
    });
  },

  setPanelWidth(windowId, tabId, width) {
    const clamped = Math.max(MIN_PANEL_WIDTH, Math.min(MAX_PANEL_WIDTH, width));
    set((state) => {
      const key = `${windowId}:${tabId}`;
      const existing = state.panels[key];
      if (!existing) return state;
      return {
        panels: {
          ...state.panels,
          [key]: { ...existing, width: clamped },
        },
      };
    });
  },

  bumpEventAt(windowId, tabId, agentId, at) {
    set((state) => ({
      lastEventAt: {
        ...state.lastEventAt,
        [`${windowId}:${tabId}:${agentId}`]: at ?? Date.now(),
      },
    }));
  },

  isWatching(windowId, tabId, agentId) {
    const ts = get().lastEventAt[`${windowId}:${tabId}:${agentId}`];
    if (!ts) return false;
    return Date.now() - ts < WATCHING_DECAY_MS;
  },
}));

// Re-export the constants for tests/callers
export { MIN_PANEL_WIDTH, MAX_PANEL_WIDTH, DEFAULT_PANEL_WIDTH, WATCHING_DECAY_MS };
