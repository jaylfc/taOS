import { useEffect } from "react";
import { useNotificationStore } from "@/stores/notification-store";
import { useDecisionEventsStore } from "@/stores/decision-events-store";
import { mapRow, ServerNotificationRow } from "@/lib/server-notifications";

type EventPayload = Record<string, unknown>;
type EventHandler = (payload: EventPayload) => void;

/**
 * Dispatch table: maps SSE event type → handler.
 * Adding a new event type is one line here.
 */
const handlers: Record<string, EventHandler> = {
  "notification.added": (payload) => {
    const store = useNotificationStore.getState();
    const newItem = mapRow(payload as unknown as ServerNotificationRow);
    // mergeServerNotifications replaces ALL unarchived srv-* items with the
    // passed list. For a single SSE push we must include the existing items so
    // they are not dropped; de-dup by id so the new item is not duplicated.
    const existingSrv = store.notifications.filter(
      (n) => n.id.startsWith("srv-") && !n.archived && n.id !== newItem.id,
    );
    store.mergeServerNotifications([newItem, ...existingSrv]);
  },
  "decision.answered": (payload) => {
    const decisionId = payload["decision_id"] as string | undefined;
    if (decisionId) useDecisionEventsStore.getState().recordAnswered(decisionId);
  },
};

/**
 * Open ONE persistent SSE connection to /api/events/stream and route each
 * incoming event by its ``type`` field through the dispatch table.
 *
 * Mount once in the app shell (App.tsx) so there is exactly one connection
 * per session.  The browser only auto-reconnects EventSource on transient
 * network drops; an HTTP error response (e.g. a 401 after session expiry)
 * closes the connection for good, so that case is reconnected manually.
 * Unmount closes the connection cleanly and cancels any pending reconnect.
 */
const RECONNECT_DELAY_MS = 5000;

// Replay on (re)connect is best-effort (see event_stream.py docstring): the
// server cannot filter by Last-Event-ID, so the same buffered events can
// arrive again. De-dupe by the event's stable id (trace_id) instead, bounded
// so the set can't grow without limit over a long session.
const MAX_SEEN_IDS = 128;

// Reconnect backoff so a permanently-down backend is not hit every 5s forever.
const MAX_RECONNECT_DELAY_MS = 30000;

export function useEventStream(): void {
  useEffect(() => {
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectAttempts = 0;
    let stopped = false;
    const seenIds: string[] = [];
    const seen = new Set<string>();

    const alreadySeen = (id: string | undefined): boolean => {
      if (!id) return false;
      if (seen.has(id)) return true;
      seen.add(id);
      seenIds.push(id);
      if (seenIds.length > MAX_SEEN_IDS) {
        const oldest = seenIds.shift();
        if (oldest) seen.delete(oldest);
      }
      return false;
    };

    const connect = () => {
      es = new EventSource("/api/events/stream");

      es.onopen = () => {
        // A clean open means the backend is healthy again; reset the backoff.
        reconnectAttempts = 0;
      };

      es.onmessage = (msg) => {
        let event: { type?: string; payload?: EventPayload; id?: string } | null;
        try {
          event = JSON.parse(msg.data as string);
        } catch {
          return;
        }
        if (!event || typeof event !== "object") return;
        if (alreadySeen(event.id)) return;
        const handler = handlers[event.type ?? ""];
        if (handler && event.payload !== undefined) {
          handler(event.payload as EventPayload);
        }
      };

      es.onerror = () => {
        // Transient network errors: the browser reconnects automatically.
        // A hard close (e.g. HTTP error response) leaves readyState CLOSED
        // and the browser gives up, so reconnect manually after a backoff.
        if (!stopped && es?.readyState === EventSource.CLOSED) {
          const delay = Math.min(
            RECONNECT_DELAY_MS * 2 ** reconnectAttempts,
            MAX_RECONNECT_DELAY_MS,
          );
          reconnectAttempts += 1;
          reconnectTimer = setTimeout(() => {
            if (!stopped) connect();
          }, delay);
        }
      };
    };

    connect();

    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      es?.close();
    };
  }, []);
}
