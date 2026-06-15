import { create } from "zustand";

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
}

interface NotificationStore {
  notifications: Notification[];
  centreOpen: boolean;

  addNotification: (n: Omit<Notification, "id" | "read" | "timestamp">) => string;
  mergeServerNotifications: (items: Notification[]) => void;
  markRead: (id: string) => void;
  markAllRead: () => void;
  dismiss: (id: string) => void;
  clearAll: () => void;
  toggleCentre: () => void;
  closeCentre: () => void;
  unreadCount: () => number;
}

let counter = 0;

// Server items dismissed this session. The backend has no "dismiss" concept
// (only read), so without this a dismissed server notification would be
// re-added by the very next poll. Session-scoped: a hard reload re-shows
// items that are still unread on the server, which is the intended behaviour.
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
      // Keep every client-origin item ("notif-N") untouched, drop the old
      // server items (replaced by the fresh list), de-dupe, sort newest-first.
      const client = s.notifications.filter((n) => !n.id.startsWith("srv-"));
      const combined = [...merged, ...client];
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
    if (id.startsWith("srv-")) dismissedServerIds.add(id);
    set((s) => ({ notifications: s.notifications.filter((n) => n.id !== id) }));
  },

  clearAll() {
    set((s) => {
      for (const n of s.notifications) {
        if (n.id.startsWith("srv-")) dismissedServerIds.add(n.id);
      }
      return { notifications: [] };
    });
  },

  toggleCentre() {
    set((s) => ({ centreOpen: !s.centreOpen }));
  },

  closeCentre() {
    set({ centreOpen: false });
  },

  unreadCount() {
    return get().notifications.filter((n) => !n.read).length;
  },
}));
