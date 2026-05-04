/**
 * agent-ws-bridge — parent-side WebSocket for one (windowId, tabId, agentId).
 *
 * Mints a copilot ticket then opens a WS to /api/desktop/browser/copilot.
 * Server events arrive as { event: "page-changed", ... }; the bridge
 * normalises them to { kind: "page-changed", ... } to match AgentEvent in
 * browser-agent-store.
 *
 * No reconnect logic in PR 6. WS closes → stays closed until next mount.
 * PR 7 will add reconnect-with-backoff if UX warrants it.
 */
import { mintCopilotTicket } from "@/lib/browser-agent-api";
import type { AgentEvent } from "@/stores/browser-agent-store";

export interface AgentWsBridgeOptions {
  windowId: string;
  tabId: string;
  agentId: string;
  profileId: string;
  /** Called when the WS receives an event. Caller dispatches to stores. */
  onEvent: (event: AgentEvent) => void;
  /** Called once when the WS opens, useful for tests. */
  onOpen?: () => void;
  /** Called when the WS closes (any reason). */
  onClose?: () => void;
}

export interface AgentWsHandle {
  close(): void;
  /** True after onOpen fires; helpful for tests. */
  readonly isOpen: boolean;
}

/** Mints a ticket then opens a WS to /api/desktop/browser/copilot.
 * If ticket minting fails (null), the handle's onClose fires immediately
 * and isOpen stays false — caller can decide whether to retry.
 */
export async function openParentWs(opts: AgentWsBridgeOptions): Promise<AgentWsHandle> {
  const ticket = await mintCopilotTicket(opts.profileId, opts.tabId, opts.agentId);
  let isOpen = false;
  let ws: WebSocket | null = null;
  let closed = false;

  const handle: AgentWsHandle = {
    get isOpen() { return isOpen; },
    close() {
      closed = true;
      if (ws) {
        try { ws.close(); } catch { /* ignore */ }
      }
    },
  };

  if (!ticket || closed) {
    if (opts.onClose) opts.onClose();
    return handle;
  }

  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${window.location.host}/api/desktop/browser/copilot?ticket=${encodeURIComponent(ticket.ticket)}`;
  ws = new WebSocket(url);

  ws.addEventListener("open", () => {
    isOpen = true;
    if (opts.onOpen) opts.onOpen();
  });

  ws.addEventListener("message", (ev) => {
    let raw: Record<string, unknown>;
    try { raw = JSON.parse(ev.data as string); } catch { return; }
    if (!raw || typeof raw !== "object") return;

    // Server sends { event: "page-changed", url?, title?, ... }
    // Normalise to AgentEvent shape: { kind, url?, title?, timestamp }
    const serverKind = typeof raw.event === "string" ? raw.event : null;
    if (!serverKind) return;

    const kind = serverKind as AgentEvent["kind"];
    // Acceptable kinds — guard against unknown event types
    if (kind !== "page-changed" && kind !== "url-changed" && kind !== "scroll") return;

    const event: AgentEvent = {
      kind,
      url: typeof raw.url === "string" ? raw.url : undefined,
      title: typeof raw.title === "string" ? raw.title : undefined,
      timestamp: typeof raw.timestamp === "number" ? raw.timestamp : Date.now(),
    };

    opts.onEvent(event);
  });

  ws.addEventListener("close", () => {
    isOpen = false;
    if (opts.onClose) opts.onClose();
  });

  return handle;
}
