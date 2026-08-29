import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { NotificationCentre } from "./NotificationCentre";
import type { Notification } from "@/stores/notification-store";

const openWindow = vi.fn();
const markRead = vi.fn();
const closeCentre = vi.fn();
const markAllRead = vi.fn();
const clearAll = vi.fn();
const dismiss = vi.fn();
const archiveRead = vi.fn();
const archivedNotifications = vi.fn(() => []);
const clearArchived = vi.fn();

let notifications: Notification[] = [];

vi.mock("@/stores/notification-store", () => ({
  useNotificationStore: (sel?: (s: unknown) => unknown) => {
    const state = {
      notifications,
      centreOpen: true,
      closeCentre,
      markRead,
      markAllRead,
      clearAll,
      dismiss,
      archiveRead,
      archivedNotifications,
      clearArchived,
    };
    if (typeof sel === "function") return sel(state);
    return state;
  },
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

  it("caps the inbox at 10 items and shows overflow indicator", () => {
    notifications = Array.from({ length: 12 }, (_, i) =>
      notif({ id: `srv-${i}`, title: `Note ${i}` }),
    );
    render(<NotificationCentre />);

    // Only the first 10 are visible in the inbox.
    expect(screen.getByText("Note 0")).toBeInTheDocument();
    expect(screen.getByText("Note 9")).toBeInTheDocument();
    expect(screen.queryByText("Note 10")).not.toBeInTheDocument();

    // Shows overflow indicator text.
    expect(screen.getByText(/\+2 more in archive/)).toBeInTheDocument();
  });

  it("does not show overflow indicator when there are 10 or fewer items", () => {
    notifications = Array.from({ length: 10 }, (_, i) =>
      notif({ id: `srv-${i}`, title: `Note ${i}` }),
    );
    render(<NotificationCentre />);
    expect(screen.queryByText(/more in archive/)).not.toBeInTheDocument();
  });

  it("renders a View archive button that opens the notifications window with section archive", () => {
    notifications = [
      notif({ id: "srv-1", title: "Active note" }),
      { ...notif({ id: "srv-x", title: "Old note" }), archived: true } as Notification,
    ];
    render(<NotificationCentre />);

    const viewArchive = screen.getByRole("button", { name: /view archive/i });
    expect(viewArchive).toBeInTheDocument();
    expect(viewArchive.textContent).toContain("(1)");

    fireEvent.click(viewArchive);

    expect(openWindow).toHaveBeenCalledWith(
      "notifications",
      expect.objectContaining({ w: 900, h: 600 }),
      { section: "archive" },
    );
  });

  it("shows no archive count when there are no archived items", () => {
    notifications = [notif({ id: "srv-1", title: "Only active" })];
    render(<NotificationCentre />);

    const viewArchive = screen.getByRole("button", { name: /view archive/i });
    expect(viewArchive).toBeInTheDocument();
    expect(viewArchive.textContent).not.toContain("(");
  });

  it("renders inline consent actions for an auth_requests notification", () => {
    notifications = [
      notif({
        id: "srv-3",
        source: "auth_requests",
        title: "Access request",
        data: { request_id: "req-1", requested_scopes: ["memory_read"] },
      }),
    ];
    render(<NotificationCentre />);

    expect(screen.getByRole("button", { name: /allow/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /deny/i })).toBeInTheDocument();
  });

  it("archives (not just removes) the notification when a consent decision is made", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({}) }),
    );
    notifications = [
      notif({
        id: "srv-4",
        source: "auth_requests",
        title: "Access request",
        data: { request_id: "req-9", requested_scopes: ["memory_read"] },
      }),
    ];
    render(<NotificationCentre />);

    fireEvent.click(screen.getByRole("button", { name: /allow/i }));

    // Resolution archives + reads (lands in History), it is NOT a plain dismiss.
    await waitFor(() => expect(archiveRead).toHaveBeenCalledWith("srv-4"));
    expect(dismiss).not.toHaveBeenCalled();
    // Clicking Allow/Deny must NOT open any app window (no row-navigation side effect).
    expect(openWindow).not.toHaveBeenCalled();
  });
});
