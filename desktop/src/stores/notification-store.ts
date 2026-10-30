import { create } from "zustand";
import { archiveServerNotification } from "@/lib/server-notifications";

export interface Notification {
  id: string;
  source: string;       // agent ID, "system", or app ID
  title: string;
  body: string;
  icon?: string;        // lucide icon name
  level: "info" | "success" | "warning" | "error";
  action?: string;      // URL or app ID to open on click
  read: boolean;
  timestamp: number;
  /** Extra typed payload for structured notifications like agent.paused. */
  meta?: Record<string, string>;
  /** Structured JSON payload from the backend (e.g. an auth-request's
   *  request_id + requested_scopes for inline consent actions). */
  data?: Record<string, unknown>;
  /** When true the notification has been dismissed/archived. */
  archived?: boolean;
}

interface NotificationStore {
  notifications: Notification[];
  centreOpen: boolean;

  addNotification: (n: Omit<Notification, "id" | "read" | "timestamp">) => string;
  mergeServerNotifications: (items: Notification[]) => void;
  markRead: (id: string) => void;
  markAllRead: () => void;
  dismiss: (id: string) => void;
  archiveRead: (id: string) => void;
  clearAll: () => void;
  toggleCentre: () => void;
  closeCentre: () => void;
  unreadCount: () => number;
  archivedNotifications: () => Notification[];
  clearArchived: () => void;
}

let counter = 0;

// Notification sources that must stay pinned at the top of the active inbox
// until they are archived (i.e. the server has decided the request). Reading a
// pinned row marks it read but never unpins it; only archive removes it.
export const PINNED_SOURCES = new Set(["auth_requests", "agent_scope_requests"]);

/** A notification is pinned when it is still active and carries a grant-request source. */
export function isPinned(n: Notification): boolean {
  return !n.archived && PINNED_SOURCES.has(n.source);
}

// Server items archived this session. Archiving POSTs to the backend
// (archiveServerNotification), which is the durable source of truth: once the
// write lands, GET /api/notifications no longer returns the row. This Set is an
// optimistic guard covering the window between the POST and the next poll, so a
// just-archived item is not briefly re-shown as active.
const dismissedServerIds = new Set<string>();

export const useNotificationStore = create<NotificationStore>((set, get) => ({
  notifications: [],
  centreOpen: false,

  addNotification(n) {
    const id = `notif-${++counter}`;
    const notif: Notification = {
      ...n,
      id,
      read: false,
      timestamp: Date.now(),
    };
    set((s) => ({ notifications: [notif, ...s.notifications].slice(0, 100) }));
    return id;
  },

  mergeServerNotifications(items) {
    set((s) => {
      // Read state already applied locally for a server item must survive a
      // poll, so the fresh row is OR-ed with the prior local read flag.
      const priorRead = new Map<string, boolean>();
      for (const n of s.notifications) {
        if (n.id.startsWith("srv-") && n.read) priorRead.set(n.id, true);
      }
      const merged = items
        .filter((n) => !dismissedServerIds.has(n.id))
        .map((n) => (priorRead.get(n.id) ? { ...n, read: true } : n));
      // Keep every client-origin item ("notif-N") untouched, plus any archived
      // server items (they must survive polls). Drop the old unarchived server
      // items (replaced by the fresh list), de-dupe, sort newest-first.
      const kept = s.notifications.filter(
        (n) => !n.id.startsWith("srv-") || n.archived,
      );
      const combined = [...merged, ...kept];
      // Pinned grant requests always lead the active inbox (newest-first among
      // them), then everything else newest-first. The 100-item cap applies
      // only to the non-pinned remainder so a pinned row is never trimmed.
      const pinned = combined.filter((n) => isPinned(n));
      const rest = combined.filter((n) => !isPinned(n));
      pinned.sort((a, b) => b.timestamp - a.timestamp);
      rest.sort((a, b) => b.timestamp - a.timestamp);
      return { notifications: [...pinned, ...rest.slice(0, 100)] };
    });
  },

  markRead(id) {
    set((s) => ({
      notifications: s.notifications.map((n) => (n.id === id ? { ...n, read: true } : n)),
    }));
  },

  markAllRead() {
    set((s) => ({ notifications: s.notifications.map((n) => ({ ...n, read: true })) }));
  },

  dismiss(id) {
    set((s) => {
      const newNotifications = s.notifications.map((n) => {
        if (n.id === id) {
          if (n.source === "agent_scope_requests" || n.source === "auth_requests") {
            return { ...n, read: true };
          }
          if (n.id.startsWith("srv-")) {
            dismissedServerIds.add(n.id);
            void archiveServerNotification(id);
          }
          return { ...n, archived: true };
        }
        return n;
      });
      return { notifications: newNotifications };
    });
  },

  archiveRead(id) {
    // Resolving a notification (e.g. answering an agent access-request) both
    // reads and archives it: it leaves the active Inbox and lands in History
    // rather than being marked read in place or silently removed (#62).
    if (id.startsWith("srv-")) {
      dismissedServerIds.add(id);
      void archiveServerNotification(id);
    }
    set((s) => ({
      notifications: s.notifications.map((n) =>
        n.id === id ? { ...n, archived: true, read: true } : n,
      ),
    }));
  },

  clearAll() {
    set((s) => {
      const newNotifications = s.notifications.map((n) => {
        if (n.id.startsWith("srv-")) {
          // Do not archive agent_scope_requests or auth_requests notifications
          if (n.source === "agent_scope_requests" || n.source === "auth_requests") {
            return n;
          }
          dismissedServerIds.add(n.id);
          void archiveServerNotification(n.id);
          return { ...n, archived: true };
        }
        // Local (non-srv-) notifications: archive them as before
        return { ...n, archived: true };
      });
      return { notifications: newNotifications };
    });
  },

  toggleCentre() {
    set((s) => ({ centreOpen: !s.centreOpen }));
  },

  closeCentre() {
    set({ centreOpen: false });
  },

  unreadCount() {
    return get().notifications.filter((n) => !n.read && !n.archived).length;
  },

  archivedNotifications() {
    return get().notifications
      .filter((n) => n.archived)
      .sort((a, b) => b.timestamp - a.timestamp);
  },

  clearArchived() {
    set((s) => ({
      notifications: s.notifications.filter((n) => !n.archived),
    }));
  },
}));
