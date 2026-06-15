import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { NotificationCentre } from "./NotificationCentre";
import type { Notification } from "@/stores/notification-store";

const openWindow = vi.fn();
const markRead = vi.fn();
const closeCentre = vi.fn();
const markAllRead = vi.fn();
const clearAll = vi.fn();
const dismiss = vi.fn();

let notifications: Notification[] = [];

vi.mock("@/stores/notification-store", () => ({
  useNotificationStore: () => ({
    notifications,
    centreOpen: true,
    closeCentre,
    markRead,
    markAllRead,
    clearAll,
    dismiss,
  }),
}));

vi.mock("@/stores/process-store", () => ({
  useProcessStore: (sel: (s: { openWindow: typeof openWindow }) => unknown) =>
    sel({ openWindow }),
}));

vi.mock("@/lib/server-notifications", () => ({
  markServerRead: vi.fn(),
  markAllServerRead: vi.fn(),
}));

vi.mock("./SetupChecklist", () => ({ SetupChecklist: () => null }));

function notif(over: Partial<Notification>): Notification {
  return {
    id: "srv-1",
    source: "system",
    title: "Title",
    body: "Body",
    level: "info",
    read: false,
    timestamp: Date.now(),
    ...over,
  };
}

describe("NotificationCentre click routing", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    notifications = [];
  });

  it("opens the target app with meta props, marks read, and closes on an action click", () => {
    notifications = [
      notif({ id: "srv-9", title: "Disk low", action: "settings", meta: { section: "storage" } }),
    ];
    render(<NotificationCentre />);

    fireEvent.click(screen.getByText("Disk low"));

    expect(openWindow).toHaveBeenCalledTimes(1);
    const [appId, , props] = openWindow.mock.calls[0];
    expect(appId).toBe("settings");
    expect(props).toEqual({ section: "storage" });
    expect(markRead).toHaveBeenCalledWith("srv-9");
    expect(closeCentre).toHaveBeenCalledTimes(1);
  });

  it("passes undefined props when the action has no meta", () => {
    notifications = [notif({ id: "srv-5", title: "Worker joined", action: "cluster" })];
    render(<NotificationCentre />);

    fireEvent.click(screen.getByText("Worker joined"));

    const [appId, , props] = openWindow.mock.calls[0];
    expect(appId).toBe("cluster");
    expect(props).toBeUndefined();
    expect(closeCentre).toHaveBeenCalledTimes(1);
  });

  it("only marks read (no navigation) for action-less notifications", () => {
    notifications = [notif({ id: "srv-2", title: "Plain note" })];
    render(<NotificationCentre />);

    fireEvent.click(screen.getByText("Plain note"));

    expect(openWindow).not.toHaveBeenCalled();
    expect(closeCentre).not.toHaveBeenCalled();
    expect(markRead).toHaveBeenCalledWith("srv-2");
  });
});
