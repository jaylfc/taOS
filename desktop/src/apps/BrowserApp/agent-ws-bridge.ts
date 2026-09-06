/**
 * agent-ws-bridge — parent-side listener for server events from copilot.js.
 *
 * The iframe (via copilot.js) holds the actual WebSocket connection to
 * /api/desktop/browser/copilot. Server events ({event: "page-changed"}, etc.)
 * are forwarded by copilot.js to the parent shell via postMessage, so the
 * parent doesn't need its own WebSocket.
 *
 * Why no parent WS? Both connections would register in CopilotHub under the
 * same (user, profile, tab, agent) key, and the hub deliberately closes the
 * prior connection on key collision. The two would clobber each other.
 * Forwarding via postMessage from copilot.js is cleaner and avoids the second
 * ticket mint per pinned agent.
 *
 * postMessage shape (sent from copilot.js, see copilot.js openConnection):
 *   { type: "taos-copilot:server-event", agentId, message }
 *
 * `message` is the raw server payload — { event: "page-changed", url, title, ... }
 */
import type { AgentEvent } from "@/stores/browser-agent-store";
import { useBrowserAgentStore } from "@/stores/browser-agent-store";

export interface AgentWsBridgeOptions {
  windowId: string;
  tabId: string;
  agentId: string;
  /** The iframe element. Used to verify e.source matches before accepting. */
  iframe: HTMLIFrameElement;
  /** Called when the iframe forwards a server event. */
  onEvent: (event: AgentEvent) => void;
  /** Kept for API compatibility; never fires (no separate WS). */
  onOpen?: () => void;
  /** Called when the listener is removed via close(). */
  onClose?: () => void;
}

export interface AgentWsHandle {
  close(): void;
  /** Always true while the listener is registered; false after close(). */
  readonly isOpen: boolean;
}

/** Milliseconds before a "driving" state auto-decays to "idle" if no further
 * driving-state event arrives. Matches the server-side drive_sessions TTL. */
export const DRIVING_DECAY_MS = 30_000;

const ALLOWED_EVENT_KINDS: ReadonlyArray<string> = [
  "page-changed",
  "url-changed",
  "scroll",
  "driving-state",
  "capability-needed",
];

/** Register a window-level postMessage listener filtered to events forwarded
 * from this (windowId, tabId, agentId) iframe. Returns a handle whose close()
 * removes the listener.
 *
 * Synchronous — no ticket round-trip needed. The iframe's own WebSocket is
 * authenticated separately (see TabRenderer's iframe-side ticket flow).
 */
export function openParentWs(opts: AgentWsBridgeOptions): AgentWsHandle {
  let isOpen = true;
  let drivingDecayTimer: ReturnType<typeof setTimeout> | null = null;

  function handleMessage(e: MessageEvent) {
    // SECURITY: only accept messages from this specific iframe. The iframe
    // can't fake e.source; cross-frame messages from other origins won't
    // pass this check.
    if (e.source !== opts.iframe.contentWindow) return;

    const data = e.data;
    if (!data || typeof data !== "object") return;
    if (data.type !== "taos-copilot:server-event") return;
    if (data.agentId !== opts.agentId) return;

    const msg = data.message;
    if (!msg || typeof msg !== "object") return;

    const serverKind = typeof msg.event === "string" ? msg.event : null;
    if (!serverKind || !ALLOWED_EVENT_KINDS.includes(serverKind)) return;

    if (serverKind === "driving-state") {
      handleDrivingState(msg);
      return;
    }

    if (serverKind === "capability-needed") {
      handleCapabilityNeeded(msg);
      return;
    }

    // The remaining kinds are AgentEvent shapes — forward to onEvent.
    const event: AgentEvent = {
      kind: serverKind as AgentEvent["kind"],
      url: typeof msg.url === "string" ? msg.url : undefined,
      title: typeof msg.title === "string" ? msg.title : undefined,
      timestamp: typeof msg.timestamp === "number" ? msg.timestamp : Date.now(),
    };

    opts.onEvent(event);
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function handleDrivingState(msg: any) {
    const state = msg.state === "driving" ? "driving" : "idle";
    useBrowserAgentStore.getState().setDrivingState(
      opts.windowId, opts.tabId, opts.agentId, state,
    );

    if (drivingDecayTimer !== null) {
      clearTimeout(drivingDecayTimer);
      drivingDecayTimer = null;
    }
    if (state === "driving") {
      // 30s idle decay — if no further driving event arrives within this
      // window, flip back to idle. Mirrors server-side drive_sessions TTL.
      drivingDecayTimer = setTimeout(() => {
        drivingDecayTimer = null;
        useBrowserAgentStore.getState().setDrivingState(
          opts.windowId, opts.tabId, opts.agentId, "idle",
        );
      }, DRIVING_DECAY_MS);
    }
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function handleCapabilityNeeded(msg: any) {
    // Dispatch a window event for CapabilityPromptModal (Task 6) to consume.
    const detail = {
      profileId: typeof msg.profile_id === "string" ? msg.profile_id : "",
      agentId: opts.agentId,
      agentName: typeof msg.agent_name === "string" ? msg.agent_name : undefined,
      permission: typeof msg.permission === "string" ? msg.permission : "",
      host: typeof msg.host === "string" ? msg.host : "",
      fullUrl: typeof msg.full_url === "string" ? msg.full_url : (typeof msg.url === "string" ? msg.url : ""),
    };
    window.dispatchEvent(new CustomEvent("taos-browser:capability-prompt", { detail }));
  }

  window.addEventListener("message", handleMessage);

  return {
    get isOpen() { return isOpen; },
    close() {
      if (!isOpen) return;
      isOpen = false;
      if (drivingDecayTimer !== null) {
        clearTimeout(drivingDecayTimer);
        drivingDecayTimer = null;
      }
      window.removeEventListener("message", handleMessage);
      if (opts.onClose) opts.onClose();
    },
  };
}
