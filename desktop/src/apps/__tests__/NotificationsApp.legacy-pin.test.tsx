import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { NotificationsApp } from "../NotificationsApp";

const mockOpenWindow = vi.fn();

vi.mock("@/stores/process-store", () => ({
  useProcessStore: (sel: (s: { openWindow: typeof mockOpenWindow }) => unknown) =>
    sel({ openWindow: mockOpenWindow }),
}));

const mockNotifications: any[] = [];
const mockMarkRead = vi.fn();
const mockMarkAllRead = vi.fn();
const mockClearAll = vi.fn();
const mockDismiss = vi.fn();
const mockArchiveRead = vi.fn();

vi.mock("@/stores/notification-store", () => ({
  useNotificationStore: (sel?: (s: unknown) => unknown) => {
    const state = {
      notifications: mockNotifications,
      markRead: mockMarkRead,
      markAllRead: mockMarkAllRead,
      clearAll: mockClearAll,
      dismiss: mockDismiss,
      archiveRead: mockArchiveRead,
    };
    if (typeof sel === "function") return sel(state);
    return state;
  },
}));

vi.mock("@/components/SetupChecklist", () => ({ SetupChecklist: () => null }));
vi.mock("@/components/ConsentActions", () => ({ ConsentActions: () => null }));
vi.mock("@/lib/server-notifications", () => ({
  markServerRead: vi.fn(),
  markAllServerRead: vi.fn(),
  mapRow: (row: unknown) => row,
}));

describe("NotificationsApp legacy pin", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNotifications.length = 0;
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve([]),
      } as unknown as Response),
    ) as typeof fetch;
  });

  it("selects the Archive tab when opened with section='archive'", async () => {
    render(<NotificationsApp windowId="w1" section="archive" />);

    await waitFor(() => {
      const archiveTab = screen.getByRole("tab", { name: /archive/i });
      expect(archiveTab).toHaveAttribute("data-state", "active");
    });
  });
});
