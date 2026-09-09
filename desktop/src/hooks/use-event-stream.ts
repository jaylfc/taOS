import { useEffect } from "react";
import { useNotificationStore } from "@/stores/notification-store";
import { useDecisionEventsStore } from "@/stores/decision-events-store";
import { mapRow, ServerNotificationRow } from "@/lib/server-notifications";
import { createSseConnection, MAX_SEEN_IDS } from "@/lib/sse";

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
 * per session.  On a hard close (e.g. HTTP error response) the shared
 * `createSseConnection` reconnects manually with backoff. Unmount closes the
 * connection cleanly and cancels any pending reconnect.
 */

export function useEventStream(): void {
  useEffect(() => {
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

    return createSseConnection({
      url: "/api/events/stream",
      getMessageId: (msg) => {
        let event: { id?: string } | null;
        try {
          event = JSON.parse(msg.data as string);
        } catch {
          return undefined;
        }
        return event?.id;
      },
      onMessage: (msg) => {
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
      },
    });
  }, []);
}
