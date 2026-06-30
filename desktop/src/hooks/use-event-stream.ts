import { useEffect } from "react";
import { useNotificationStore } from "@/stores/notification-store";
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
};

/**
 * Open ONE persistent SSE connection to /api/events/stream and route each
 * incoming event by its ``type`` field through the dispatch table.
 *
 * Mount once in the app shell (App.tsx) so there is exactly one connection
 * per session.  The EventSource reconnects automatically on transient errors;
 * unmount closes the connection cleanly.
 */
export function useEventStream(): void {
  useEffect(() => {
    const es = new EventSource("/api/events/stream");

    es.onmessage = (msg) => {
      let event: { type?: string; payload?: EventPayload } | null;
      try {
        event = JSON.parse(msg.data as string);
      } catch {
        return;
      }
      if (!event || typeof event !== "object") return;
      const handler = handlers[event.type ?? ""];
      if (handler && event.payload !== undefined) {
        handler(event.payload as EventPayload);
      }
    };

    es.onerror = () => {
      // Transient network errors: the browser reconnects automatically.
      // On hard close the effect cleanup runs and a remount opens a fresh stream.
    };

    return () => es.close();
  }, []);
}
