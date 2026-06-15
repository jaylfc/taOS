import { beforeEach, describe, expect, it } from "vitest";
import { useNotificationStore, type Notification } from "./notification-store";

function srv(id: number, ts: number, read = false): Notification {
  return {
    id: `srv-${id}`,
    source: "system",
    title: `t${id}`,
    body: `b${id}`,
    level: "info",
    read,
    timestamp: ts,
  };
}

beforeEach(() => {
  useNotificationStore.setState({ notifications: [], centreOpen: false });
});

describe("mergeServerNotifications", () => {
  it("upserts server items newest-first without duplicates across two polls", () => {
    const store = useNotificationStore.getState();

    store.mergeServerNotifications([srv(1, 100), srv(2, 200)]);
    let ids = useNotificationStore.getState().notifications.map((n) => n.id);
    expect(ids).toEqual(["srv-2", "srv-1"]);

    // Second poll returns the same two plus a newer one. No dupes.
    store.mergeServerNotifications([srv(1, 100), srv(2, 200), srv(3, 300)]);
    ids = useNotificationStore.getState().notifications.map((n) => n.id);
    expect(ids).toEqual(["srv-3", "srv-2", "srv-1"]);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("preserves a server item marked read locally even if the poll says unread", () => {
    const store = useNotificationStore.getState();
    store.mergeServerNotifications([srv(1, 100, false)]);

    // User reads it locally (optimistic), backend write may not have landed.
    store.markRead("srv-1");
    expect(useNotificationStore.getState().notifications[0].read).toBe(true);

    // Next poll still reports it unread; local read must survive.
    store.mergeServerNotifications([srv(1, 100, false)]);
    expect(useNotificationStore.getState().notifications[0].read).toBe(true);
  });

  it("keeps client-origin (notif-) items untouched", () => {
    const store = useNotificationStore.getState();
    const clientId = store.addNotification({ source: "system", title: "welcome", body: "hi", level: "info" });

    store.mergeServerNotifications([srv(1, 50)]);

    const ids = useNotificationStore.getState().notifications.map((n) => n.id);
    expect(ids).toContain(clientId);
    expect(ids).toContain("srv-1");
  });

  it("replaces stale server items with the fresh list (removed ones drop out)", () => {
    const store = useNotificationStore.getState();
    store.mergeServerNotifications([srv(1, 100), srv(2, 200)]);

    // A later poll no longer includes srv-2.
    store.mergeServerNotifications([srv(1, 100)]);
    const ids = useNotificationStore.getState().notifications.map((n) => n.id);
    expect(ids).toEqual(["srv-1"]);
  });

  it("does not resurrect a dismissed server item on the next poll", () => {
    const store = useNotificationStore.getState();
    // Unique id: dismissedServerIds is module-scoped and persists across tests.
    store.mergeServerNotifications([srv(901, 100)]);
    expect(useNotificationStore.getState().notifications.map((n) => n.id)).toContain("srv-901");

    store.dismiss("srv-901");
    expect(useNotificationStore.getState().notifications.map((n) => n.id)).not.toContain("srv-901");

    // Backend still reports it (no server-side dismiss); it must stay hidden.
    store.mergeServerNotifications([srv(901, 100)]);
    expect(useNotificationStore.getState().notifications.map((n) => n.id)).not.toContain("srv-901");
  });

  it("caps the merged list at 100 items", () => {
    const store = useNotificationStore.getState();
    const many = Array.from({ length: 150 }, (_, i) => srv(i, i));
    store.mergeServerNotifications(many);
    expect(useNotificationStore.getState().notifications).toHaveLength(100);
  });
});
