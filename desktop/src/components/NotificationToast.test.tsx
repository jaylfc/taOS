import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { NotificationToasts } from "./NotificationToast";

const mockDismiss = vi.fn();
const mockArchiveRead = vi.fn();
const mockNotifications: Array<{
  id: string;
  source: string;
  title: string;
  body: string;
  level: "info" | "success" | "warning" | "error";
  read: boolean;
  timestamp: number;
  data?: Record<string, unknown>;
}> = [];

vi.mock("@/stores/notification-store", () => ({
  useNotificationStore: (
    selector: (state: {
      notifications: typeof mockNotifications;
      dismiss: typeof mockDismiss;
      archiveRead: typeof mockArchiveRead;
    }) => unknown,
  ) =>
    selector({
      notifications: mockNotifications,
      dismiss: mockDismiss,
      archiveRead: mockArchiveRead,
    }),
}));

vi.mock("@/stores/process-store", () => ({
  useProcessStore: () => ({ openWindow: vi.fn() }),
}));

vi.mock("@/registry/app-registry", () => ({
  getApp: vi.fn(),
}));

describe("NotificationToasts", () => {
  it("renders nothing when there are no notifications", () => {
    const { container } = render(<NotificationToasts />);
    expect(container.querySelector("[aria-label='Notifications']")?.children.length).toBe(0);
  });

  it("renders a notification toast with title and body", () => {
    mockNotifications.length = 0;
    mockNotifications.push({
      id: "test-1",
      source: "system",
      title: "Update available",
      body: "A new version of taOS is ready to install.",
      level: "info",
      read: false,
      timestamp: Date.now(),
    });
    render(<NotificationToasts />);
    expect(screen.getByText("Update available")).toBeInTheDocument();
    expect(screen.getByText("A new version of taOS is ready to install.")).toBeInTheDocument();
  });

  it("calls dismiss when the close button is clicked", async () => {
    mockNotifications.length = 0;
    mockNotifications.push({
      id: "test-2",
      source: "system",
      title: "Restart required",
      body: "Please restart to apply changes.",
      level: "warning",
      read: false,
      timestamp: Date.now(),
    });
    mockDismiss.mockClear();
    render(<NotificationToasts />);
    fireEvent.click(screen.getByRole("button", { name: /dismiss notification/i }));
    await waitFor(() => expect(mockDismiss).toHaveBeenCalledWith("test-2"));
  });

  it("renders consent actions for an auth_requests toast and does not auto-dismiss", () => {
    vi.useFakeTimers();
    try {
      mockNotifications.length = 0;
      mockNotifications.push({
        id: "srv-7",
        source: "auth_requests",
        title: "Access request",
        body: "owl@lab is requesting memory_read",
        level: "info",
        read: false,
        timestamp: Date.now(),
        data: { request_id: "req-1", requested_scopes: ["memory_read"] },
      });
      render(<NotificationToasts />);
      expect(screen.getByRole("button", { name: /allow/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /deny/i })).toBeInTheDocument();
      // The 5s auto-expiry timer must not hide a consent toast.
      act(() => vi.advanceTimersByTime(6000));
      expect(screen.getByText("Access request")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("archives (not just removes) the consent toast when a decision is made", async () => {
    mockDismiss.mockClear();
    mockArchiveRead.mockClear();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve({}) }),
    );
    mockNotifications.length = 0;
    mockNotifications.push({
      id: "srv-8",
      source: "auth_requests",
      title: "Access request",
      body: "owl@lab is requesting memory_read",
      level: "info",
      read: false,
      timestamp: Date.now(),
      data: { request_id: "req-2", requested_scopes: ["memory_read"] },
    });
    render(<NotificationToasts />);

    fireEvent.click(screen.getByRole("button", { name: /deny/i }));

    // Resolving archives + reads (lands in History); not a plain dismiss.
    await waitFor(() => expect(mockArchiveRead).toHaveBeenCalledWith("srv-8"));
    expect(mockDismiss).not.toHaveBeenCalled();
  });

  it("auto-expires the toast after 5s without archiving it", () => {
    vi.useFakeTimers();
    try {
      mockNotifications.length = 0;
      mockNotifications.push({
        id: "test-3",
        source: "system",
        title: "Synced",
        body: "Your files are up to date.",
        level: "success",
        read: false,
        timestamp: Date.now(),
      });
      mockDismiss.mockClear();
      render(<NotificationToasts />);
      expect(screen.getByText("Synced")).toBeInTheDocument();
      // Toast vanishes from view once the 5s timer fires...
      act(() => vi.advanceTimersByTime(5000));
      expect(screen.queryByText("Synced")).not.toBeInTheDocument();
      // ...but auto-expiry must never archive (dismiss is the explicit action).
      expect(mockDismiss).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
