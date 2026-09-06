import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import React from "react";
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

vi.mock("@/components/ui", () => ({
  Tabs: ({ children, value, onValueChange }: { children: React.ReactNode; value?: string; onValueChange?: (v: string) => void }) => {
    const items = React.Children.toArray(children);
    const active = items.filter((child: React.ReactNode) => {
      if (!React.isValidElement(child)) return false;
      return child.props.value === value;
    });
    return (
      <div data-testid="tabs" data-value={value}>
        <button data-testid="tab-notifications" onClick={() => onValueChange?.("notifications")}>Notifications</button>
        <button data-testid="tab-archive" onClick={() => onValueChange?.("archive")}>Archive</button>
        {active}
      </div>
    );
  },
  TabsContent: ({ children, value }: { children: React.ReactNode; value?: string }) => (
    <div data-testid={`tab-content-${value}`}>{children}</div>
  ),
  TabsList: ({ children }: { children: React.ReactNode }) => <div data-testid="tabs-list">{children}</div>,
  TabsTrigger: ({ children, value, onClick }: { children: React.ReactNode; value?: string; onClick?: () => void }) => (
    <button data-testid={`trigger-${value}`} onClick={onClick}>{children}</button>
  ),
}));

function mockFetch(
  resolver: (url: string) => { ok: boolean; body: unknown },
) {
  return vi.fn().mockImplementation((input: string) => {
    const hit = resolver(input);
    return Promise.resolve({
      ok: hit.ok,
      status: hit.ok ? 200 : 500,
      headers: new Headers({ "content-type": "application/json" }),
      json: () => Promise.resolve(hit.body),
    });
  });
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

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
  beforeEach(() => {
    vi.clearAllMocks();
    useNotificationStore.setState({ notifications: [], centreOpen: false });
  });

  it("renders Notifications and Archive tabs", () => {
    render(<NotificationsApp windowId="w1" />);
    expect(screen.getByTestId("tab-notifications")).toBeInTheDocument();
    expect(screen.getByTestId("tab-archive")).toBeInTheDocument();
  });

  it("defaults to the notifications tab", () => {
    render(<NotificationsApp windowId="w1" />);
    expect(screen.getByTestId("tabs")).toHaveAttribute("data-value", "notifications");
  });

  it("defaults to archive tab when section=archive prop is passed", () => {
    render(<NotificationsApp windowId="w1" section="archive" />);
    expect(screen.getByTestId("tabs")).toHaveAttribute("data-value", "archive");
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

  it("shows archived notifications in the archive tab", async () => {
    const fetchMock = mockFetch(() => ({ ok: true, body: [
      { id: "srv-2", title: "Old note", body: "", level: "info", read: true, timestamp: Date.now() - 1000, archived: true, source: "system" },
    ] }));
    vi.stubGlobal("fetch", fetchMock);
    useNotificationStore.setState({
      notifications: [
        notif({ id: "srv-1", title: "Active note" }),
        { ...notif({ id: "srv-2", title: "Old note" }), archived: true } as Notification,
      ],
    });
    render(<NotificationsApp windowId="w1" />);
    fireEvent.click(screen.getByTestId("tab-archive"));
    await flush();
    expect(screen.getByText("Old note")).toBeInTheDocument();
  });

  it("switches to archive tab when View archive button is clicked", () => {
    useNotificationStore.setState({
      notifications: [notif({ id: "srv-1", title: "Active note" })],
    });
    render(<NotificationsApp windowId="w1" />);
    fireEvent.click(screen.getByText(/view archive/i));
    expect(screen.getByTestId("tabs")).toHaveAttribute("data-value", "archive");
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

  it("preserves active notifications when the archive tab is opened", async () => {
    const fetchMock = mockFetch(() => ({ ok: true, body: [
      { id: "srv-2", title: "Old note", body: "", level: "info", read: true, timestamp: Date.now() - 1000, archived: true, source: "system" },
    ] }));
    vi.stubGlobal("fetch", fetchMock);
    useNotificationStore.setState({
      notifications: [
        notif({ id: "srv-1", title: "Active note" }),
        { ...notif({ id: "srv-2", title: "Old note" }), archived: true } as Notification,
      ],
    });
    render(<NotificationsApp windowId="w1" />);
    fireEvent.click(screen.getByTestId("tab-archive"));
    await flush();
    const all = useNotificationStore.getState().notifications;
    expect(all.map((n) => n.id)).toContain("srv-1");
    expect(all.map((n) => n.id)).toContain("srv-2");
    expect(screen.getByText("Old note")).toBeInTheDocument();
  });

  it("server-only archived rows survive a store mutation after fetch", async () => {
    const fetchMock = mockFetch(() => ({ ok: true, body: [
      { id: "srv-2", title: "Server only archived", body: "", level: "info", read: true, timestamp: Date.now() - 1000, archived: true, source: "system" },
    ] }));
    vi.stubGlobal("fetch", fetchMock);
    useNotificationStore.setState({
      notifications: [notif({ id: "srv-1", title: "Active note" })],
    });
    render(<NotificationsApp windowId="w1" />);
    fireEvent.click(screen.getByTestId("tab-archive"));
    await flush();
    expect(screen.getByText("Server only archived")).toBeInTheDocument();

    useNotificationStore.getState().markRead("srv-1");
    await flush();
    expect(screen.getByText("Server only archived")).toBeInTheDocument();
  });

  it("server-only archived rows survive clearAll", async () => {
    const fetchMock = mockFetch(() => ({ ok: true, body: [
      { id: "srv-2", title: "Server only archived", body: "", level: "info", read: true, timestamp: Date.now() - 1000, archived: true, source: "system" },
    ] }));
    vi.stubGlobal("fetch", fetchMock);
    useNotificationStore.setState({
      notifications: [notif({ id: "srv-1", title: "Active note" })],
    });
    render(<NotificationsApp windowId="w1" />);
    fireEvent.click(screen.getByTestId("tab-archive"));
    await flush();
    expect(screen.getByText("Server only archived")).toBeInTheDocument();

    useNotificationStore.getState().clearAll();
    await flush();
    expect(screen.getByText("Server only archived")).toBeInTheDocument();
  });

  it("aborts in-flight archive fetches on unmount", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      json: () => Promise.resolve([]),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NotificationsApp windowId="w1" />);

    for (let i = 0; i < 3; i++) {
      fireEvent.click(screen.getByTestId("tab-archive"));
      await flush();
      fireEvent.click(screen.getByTestId("tab-notifications"));
      await flush();
    }

    expect(fetchMock).toHaveBeenCalledTimes(3);
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

  it("reacts to initialSection prop changes", () => {
    const { rerender } = render(<NotificationsApp windowId="w1" />);
    expect(screen.getByTestId("tabs")).toHaveAttribute("data-value", "notifications");
    rerender(<NotificationsApp windowId="w1" section="archive" />);
    expect(screen.getByTestId("tabs")).toHaveAttribute("data-value", "archive");
  });

  it("aborts the in-flight archive fetch signal on unmount", async () => {
    let capturedSignal: AbortSignal | undefined;
    const fetchMock = vi.fn().mockImplementation((_input: string, init?: RequestInit) => {
      capturedSignal = init?.signal;
      return new Promise(() => {});
    });
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = render(<NotificationsApp windowId="w1" />);
    fireEvent.click(screen.getByTestId("tab-archive"));
    await flush();

    expect(capturedSignal).toBeDefined();
    expect(capturedSignal!.aborted).toBe(false);

    unmount();

    expect(capturedSignal!.aborted).toBe(true);
  });

  it("clears loading state when archive fetch rejects and avoids stale state updates on unmount", async () => {
    let rejectFetch!: (e: Error) => void;
    const fetchMock = vi.fn().mockImplementation(() => {
      return new Promise((_, reject) => {
        rejectFetch = reject;
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const { unmount } = render(<NotificationsApp windowId="w1" />);
    fireEvent.click(screen.getByTestId("tab-archive"));
    await flush();
    expect(screen.getByText("Loading archive...")).toBeInTheDocument();

    rejectFetch(new Error("Network failure"));
    await flush();

    expect(screen.queryByText("Loading archive...")).not.toBeInTheDocument();
    expect(screen.getByText("Network failure")).toBeInTheDocument();

    unmount();
    await flush();
    expect(consoleErrorSpy).not.toHaveBeenCalled();
    consoleErrorSpy.mockRestore();
  });
});

describe("APP_REDIRECTS", () => {
  it("has no unread section field on redirect entries", async () => {
    const mod = await import("@/registry/app-registry");
    for (const entry of Object.values(mod.APP_REDIRECTS)) {
      expect(entry).not.toHaveProperty("section");
    }
  });
});
