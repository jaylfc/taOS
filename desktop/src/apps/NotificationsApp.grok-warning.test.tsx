import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import React from "react";
import { NotificationsApp } from "@/apps/NotificationsApp";
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
// Do NOT mock ConsentActions: we want to render the real component so the
// harness-dependent warning text is observable by this test.

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

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

function notif(over: Partial<Notification>): Notification {
  return {
    id: "srv-1",
    source: "auth_requests",
    title: "Access request",
    body: "Agent requests access",
    level: "info",
    read: false,
    timestamp: Date.now(),
    ...over,
  };
}

describe("NotificationsApp grok harness warning", () => {
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

  it("renders the Grok shared-account warning for grok auth_requests notifications", async () => {
    useNotificationStore.setState({
      notifications: [
        notif({
          id: "srv-1",
          source: "auth_requests",
          data: {
            request_id: "req-1",
            requested_scopes: ["a2a_send"],
            framework: "grok",
          },
        }),
      ],
    });
    render(<NotificationsApp windowId="w1" />);
    await flush();
    expect(screen.getByText(/readable by all bots on this Grok account/i)).toBeInTheDocument();
  });
});
