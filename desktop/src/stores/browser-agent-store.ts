import { create } from "zustand";

const MIN_PANEL_WIDTH = 240;
const MAX_PANEL_WIDTH = 480;
const DEFAULT_PANEL_WIDTH = 280;
const WATCHING_DECAY_MS = 3000;

export interface AnnotationCursor {
  kind: "cursor";
  id: string;
  agentId: string;
  x: number;
  y: number;
  label?: string;
  color?: string;
}

export interface AnnotationArrow {
  kind: "arrow";
  id: string;
  agentId: string;
  from: { x: number; y: number };
  to: { x: number; y: number };
  color?: string;
}

export type Annotation = AnnotationCursor | AnnotationArrow;

export interface AgentPanelState {
  isOpen: boolean;
  activeAgentId: string | null;
  width: number;
}

/** A chat message in the local in-memory thread for a (tab, agent) pair. */
export interface AgentMessage {
  id: string;
  author: "user" | "agent";
  content: string;
  timestamp: number;
}

/** A recent browser event recorded per (tab, agent). */
export interface AgentEvent {
  kind: "page-changed" | "url-changed" | "scroll";
  url?: string;
  title?: string;
  timestamp: number;
}

const MAX_RECENT_EVENTS = 5;

export interface BrowserAgentState {
  /** Per-(windowId, tabId) panel state. Key: `${windowId}:${tabId}`. */
  panels: Record<string, AgentPanelState>;

  /** Per-(windowId, tabId, agentId) timestamp of the last WS event received.
   * Used by AgentPresencePill to compute the "watching" pulse state.
   * Key: `${windowId}:${tabId}:${agentId}`. */
  lastEventAt: Record<string, number>;

  /** Chat thread per (tab, agent). Key: `${windowId}:${tabId}:${agentId}`. */
  messages: Record<string, AgentMessage[]>;

  /** Recent page events per (tab, agent). Key: same. Capped at last 5 per key. */
  recentEvents: Record<string, AgentEvent[]>;

  /** Per-(window, tab) annotation overlay. Key: `${windowId}:${tabId}` */
  annotations: Record<string, Annotation[]>;

  /** Per-(window, tab, agent) driving state. Key: `${windowId}:${tabId}:${agentId}`. */
  drivingState: Record<string, "idle" | "driving">;

  openPanel(windowId: string, tabId: string, agentId: string): void;
  closePanel(windowId: string, tabId: string): void;
  togglePanel(windowId: string, tabId: string, agentId: string): void;
  setActiveAgent(windowId: string, tabId: string, agentId: string): void;
  setPanelWidth(windowId: string, tabId: string, width: number): void;
  bumpEventAt(windowId: string, tabId: string, agentId: string, at?: number): void;

  /** True if `lastEventAt` for this key is within WATCHING_DECAY_MS of now. */
  isWatching(windowId: string, tabId: string, agentId: string): boolean;

  appendMessage(windowId: string, tabId: string, agentId: string, message: AgentMessage): void;
  appendEvent(windowId: string, tabId: string, agentId: string, event: AgentEvent): void;

  addAnnotation(windowId: string, tabId: string, ann: Annotation): void;
  clearAnnotation(windowId: string, tabId: string, id: string): void;
  clearAnnotations(windowId: string, tabId: string, agentId?: string): void;

  setDrivingState(windowId: string, tabId: string, agentId: string, state: "idle" | "driving"): void;

  /** Returns the agent_id of the first driving agent for the (window, tab),
   * or null if none. Used by chrome components to decide visual state. */
  isAnyDriving(windowId: string, tabId: string): string | null;
}

export const useBrowserAgentStore = create<BrowserAgentState>((set, get) => ({
  panels: {},
  lastEventAt: {},
  messages: {},
  recentEvents: {},
  annotations: {},
  drivingState: {},

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

  appendMessage(windowId, tabId, agentId, message) {
    set((state) => {
      const key = `${windowId}:${tabId}:${agentId}`;
      const existing = state.messages[key] ?? [];
      return {
        messages: {
          ...state.messages,
          [key]: [...existing, message],
        },
      };
    });
  },

  appendEvent(windowId, tabId, agentId, event) {
    set((state) => {
      const key = `${windowId}:${tabId}:${agentId}`;
      const existing = state.recentEvents[key] ?? [];
      const updated = [...existing, event].slice(-MAX_RECENT_EVENTS);
      return {
        recentEvents: {
          ...state.recentEvents,
          [key]: updated,
        },
      };
    });
  },

  addAnnotation(windowId, tabId, ann) {
    set((state) => {
      const key = `${windowId}:${tabId}`;
      const existing = state.annotations[key] ?? [];
      const idx = existing.findIndex((a) => a.id === ann.id);
      const updated = idx >= 0
        ? existing.map((a) => (a.id === ann.id ? ann : a))
        : [...existing, ann];
      return {
        annotations: {
          ...state.annotations,
          [key]: updated,
        },
      };
    });
  },

  clearAnnotation(windowId, tabId, id) {
    set((state) => {
      const key = `${windowId}:${tabId}`;
      const existing = state.annotations[key] ?? [];
      return {
        annotations: {
          ...state.annotations,
          [key]: existing.filter((a) => a.id !== id),
        },
      };
    });
  },

  clearAnnotations(windowId, tabId, agentId) {
    set((state) => {
      const key = `${windowId}:${tabId}`;
      const existing = state.annotations[key] ?? [];
      const updated = agentId
        ? existing.filter((a) => a.agentId !== agentId)
        : [];
      return {
        annotations: {
          ...state.annotations,
          [key]: updated,
        },
      };
    });
  },

  setDrivingState(windowId, tabId, agentId, state) {
    set((s) => ({
      drivingState: {
        ...s.drivingState,
        [`${windowId}:${tabId}:${agentId}`]: state,
      },
    }));
  },

  isAnyDriving(windowId, tabId) {
    const ds = get().drivingState;
    const prefix = `${windowId}:${tabId}:`;
    for (const key of Object.keys(ds)) {
      if (key.startsWith(prefix) && ds[key] === "driving") {
        return key.slice(prefix.length);
      }
    }
    return null;
  },
}));

// Re-export constants for tests/callers
export { MIN_PANEL_WIDTH, MAX_PANEL_WIDTH, DEFAULT_PANEL_WIDTH, WATCHING_DECAY_MS, MAX_RECENT_EVENTS };
