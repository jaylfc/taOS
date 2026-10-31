import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { NotificationsApp } from "./NotificationsApp";
import { useNotificationStore, type Notification } from "@/stores/notification-store";

const openWindow = vi.fn();

vi.mock("@/stores/process-store", () => ({
  useProcessStore: (sel: (s: { openWindow: typeof openWindow }) => unknown) =>
    sel({ openWindow }),
}));

vi.mock("@/lib/server-notifications", () => ({
  markServerRead: vi.fn(),
  markAllServerRead: vi.fn(),
  archiveServerNotification: vi.fn(),
  fetchServerNotifications: vi.fn().mockResolvedValue([]),
  mapRow: (row: Record<string, unknown>) => row as Notification,
}));

vi.mock("@/lib/notifications-push", () => ({
  getPushState: vi.fn().mockResolvedValue("disabled"),
  enableNotificationsPush: vi.fn(),
  disableNotificationsPush: vi.fn(),
}));

vi.mock("@/components/SetupChecklist", () => ({ SetupChecklist: () => null }));
vi.mock("@/components/ConsentActions", () => ({ ConsentActions: () => null }));

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

describe("NotificationsApp", () => {
  const MockEventSourceCtor = vi.fn().mockImplementation(function (this: any) {
    this.url = "";
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    this.close = vi.fn();
    this.readyState = 0;
  });
  Object.assign(MockEventSourceCtor, { CONNECTING: 0, OPEN: 1, CLOSED: 2 });

  beforeEach(() => {
    vi.clearAllMocks();
    useNotificationStore.setState({ notifications: [], centreOpen: false });
    vi.stubGlobal("EventSource", MockEventSourceCtor);
    MockEventSourceCtor.mockClear();
  });

  it("shows active notifications in the notifications tab", () => {
    useNotificationStore.setState({
      notifications: [notif({ id: "srv-1", title: "Active note" })],
    });
    render(<NotificationsApp windowId="w1" />);
    expect(screen.getByText("Active note")).toBeInTheDocument();
  });

  it("shows an empty state when there are no active notifications", () => {
    render(<NotificationsApp windowId="w1" />);
    expect(screen.getByText("No notifications")).toBeInTheDocument();
  });

  it("opens the Notification History app when View history is clicked", () => {
    useNotificationStore.setState({
      notifications: [notif({ id: "srv-1", title: "Active note" })],
    });
    render(<NotificationsApp windowId="w1" />);
    fireEvent.click(screen.getByRole("button", { name: /view history/i }));
    expect(openWindow).toHaveBeenCalledTimes(1);
    const [appId, , props] = openWindow.mock.calls[0];
    expect(appId).toBe("notification-history");
    expect(props).toBeUndefined();
  });

  it("no longer renders an Archive tab", () => {
    render(<NotificationsApp windowId="w1" />);
    expect(screen.queryByRole("tab", { name: /archive/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/view archive/i)).not.toBeInTheDocument();
  });

  it("marks read and does not close on notification click when there is no action", () => {
    useNotificationStore.setState({
      notifications: [notif({ id: "srv-1", title: "Plain note" })],
    });
    render(<NotificationsApp windowId="w1" />);
    fireEvent.click(screen.getByText("Plain note"));
    expect(useNotificationStore.getState().notifications[0].read).toBe(true);
    expect(openWindow).not.toHaveBeenCalled();
  });

  it("calls markAllRead when the mark-all-read button is clicked", () => {
    useNotificationStore.setState({
      notifications: [notif({ id: "srv-1", title: "Note" })],
    });
    render(<NotificationsApp windowId="w1" />);
    fireEvent.click(screen.getByTitle("Mark all read"));
    expect(useNotificationStore.getState().notifications.every((n) => n.read)).toBe(true);
  });

  it("calls clearAll when the clear-all button is clicked", () => {
    useNotificationStore.setState({
      notifications: [notif({ id: "srv-1", title: "Note" })],
    });
    render(<NotificationsApp windowId="w1" />);
    fireEvent.click(screen.getByTitle("Clear all"));
    expect(useNotificationStore.getState().notifications.every((n) => n.archived)).toBe(true);
  });

  it("dismiss button is not nested inside another button", () => {
    useNotificationStore.setState({
      notifications: [notif({ id: "srv-1", title: "Note" })],
    });
    render(<NotificationsApp windowId="w1" />);
    const dismissBtn = screen.getByLabelText(/Dismiss:/i);
    expect(dismissBtn.closest("button")).toBe(dismissBtn);
  });

  it("shows all active notifications when there are more than INBOX_CAP", () => {
    const many = Array.from({ length: 15 }, (_, i) => notif({ id: `srv-${i}`, title: `Note ${i}` }));
    useNotificationStore.setState({ notifications: many });
    render(<NotificationsApp windowId="w1" />);
    for (let i = 0; i < 15; i++) {
      expect(screen.getByText(`Note ${i}`)).toBeInTheDocument();
    }
  });
});

describe("APP_REDIRECTS", () => {
  // The anti-inert-field gate (c01c1e5). A redirect's `section` is only
  // honest if the target app consumes it. No target app consumes one any
  // more: the Notifications Archive tab is gone (tsk-i5gygp) and
  // `notification-archive` now opens the standalone Notification History
  // app. So any declared section is inert by construction and is refused
  // here until a target app grows its own gate that proves it is consumed.
  it("declares no section, and every redirect lands on a registered app", async () => {
    const mod = await import("@/registry/app-registry");
    for (const [pinId, entry] of Object.entries(mod.APP_REDIRECTS)) {
      if (entry.section) {
        throw new Error(
          `redirect "${pinId}" declares section "${entry.section}" on app "${entry.appId}", ` +
            "which no gate proves is consumed. Add that gate beside the target app's tests first.",
        );
      }
      expect(mod.getApp(entry.appId)?.id).toBe(entry.appId);
      expect(mod.pinnedLaunchProps(pinId)).toBeUndefined();
    }
  });
});
