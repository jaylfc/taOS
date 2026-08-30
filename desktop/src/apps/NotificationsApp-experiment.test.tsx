import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import React from "react";
import { NotificationsApp } from "./NotificationsApp";
import { useNotificationStore } from "@/stores/notification-store";

vi.mock("@/stores/process-store", () => ({
  useProcessStore: (sel: (s: any) => unknown) => sel({ openWindow: vi.fn() }),
}));
vi.mock("@/lib/server-notifications", () => ({
  markServerRead: vi.fn(),
  markAllServerRead: vi.fn(),
  archiveServerNotification: vi.fn(),
  fetchServerNotifications: vi.fn().mockResolvedValue([]),
  mapRow: (row: any) => row,
}));
vi.mock("@/lib/notifications-push", () => ({
  getPushState: vi.fn().mockResolvedValue("disabled"),
  enableNotificationsPush: vi.fn(),
  disableNotificationsPush: vi.fn(),
}));
vi.mock("@/components/SetupChecklist", () => ({ SetupChecklist: () => null }));
vi.mock("@/components/ConsentActions", () => ({ ConsentActions: () => null }));
vi.mock("@/components/ui", () => ({
  Tabs: ({ children, value, onValueChange }: any) => {
    const items = React.Children.toArray(children);
    const active = items.filter((child: any) => child.props.value === value);
    return (
      <div data-testid="tabs" data-value={value}>
        <button data-testid="tab-notifications" onClick={() => onValueChange?.("notifications")}>Notifications</button>
        <button data-testid="tab-archive" onClick={() => onValueChange?.("archive")}>Archive</button>
        {active}
      </div>
    );
  },
  TabsContent: ({ children, value }: any) => <div data-testid={`tab-content-${value}`}>{children}</div>,
  TabsList: () => null,
  TabsTrigger: () => null,
}));

async function flush() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

describe("experiment", () => {
  beforeEach(() => { vi.clearAllMocks(); useNotificationStore.setState({ notifications: [], centreOpen: false }); });

  it("counts fetches across tab toggle", async () => {
    const fetchMock = vi.fn().mockImplementation(() => new Promise(() => {}));
    vi.stubGlobal("fetch", fetchMock);
    render(<NotificationsApp windowId="w1" section="archive" />);
    await flush();
    console.log("after mount calls:", fetchMock.mock.calls.length);
    fireEvent.click(screen.getByTestId("tab-notifications"));
    await flush();
    fireEvent.click(screen.getByTestId("tab-archive"));
    await flush();
    console.log("after toggle calls:", fetchMock.mock.calls.length);
    expect(true).toBe(true);
  });
});
