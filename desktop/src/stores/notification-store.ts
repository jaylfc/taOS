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
      combined.sort((a, b) => b.timestamp - a.timestamp);
      return { notifications: combined.slice(0, 100) };
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
    if (id.startsWith("srv-")) {
      dismissedServerIds.add(id);
      void archiveServerNotification(id);
    }
    set((s) => ({
      notifications: s.notifications.map((n) =>
        n.id === id ? { ...n, archived: true } : n,
      ),
    }));
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
      for (const n of s.notifications) {
        if (n.id.startsWith("srv-")) {
          dismissedServerIds.add(n.id);
          void archiveServerNotification(n.id);
        }
      }
      return {
        notifications: s.notifications.map((n) => ({ ...n, archived: true })),
      };
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
