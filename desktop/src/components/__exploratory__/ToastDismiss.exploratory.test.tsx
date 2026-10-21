import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { useNotificationStore } from "@/stores/notification-store";
import { NotificationToasts } from "@/components/NotificationToast";

// Reset the real store between cases so each test starts clean.
function resetStore() {
  useNotificationStore.setState({ notifications: [], centreOpen: false });
}

describe("mobile toast auto-dismiss", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetStore();
  });

  afterEach(() => {
    vi.useRealTimers();
    resetStore();
  });

  it("info system toast auto-dismisses on mobile despite frequent parent re-renders", () => {
    // Add an info-level system notification, the same kind SpaUpdateToast
    // posts for "New taOS build available".
    useNotificationStore.getState().addNotification({
      source: "system",
      level: "info",
      title: "New taOS build available",
      body: "Reload to update.",
    });

    // topInset mirrors the mobile shell call site in App.tsx:
    //   <NotificationToasts topInset={MOBILE_TOP_BAR_HEIGHT} />
    const { rerender } = render(<NotificationToasts topInset="calc(44px)" />);

    // Toast is visible on the home pages.
    expect(screen.getByText("New taOS build available")).toBeInTheDocument();

    // Simulate the mobile shell re-rendering the toast host while the toast
    // is visible: widget ticks, grid re-renders, store updates every ~1s.
    // Previously each re-render recreated the onExpire callback, resetting
    // the 5s dismiss timer so the toast never cleared.
    for (let i = 0; i < 10; i++) {
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      rerender(<NotificationToasts topInset="calc(44px)" />);
    }

    // Past the auto-dismiss timeout: toast is gone from the DOM...
    expect(screen.queryByText("New taOS build available")).not.toBeInTheDocument();
    // ...but the notification remains in the store so the notification
    // centre does not lose the reload prompt.
    const notifs = useNotificationStore.getState().notifications;
    expect(notifs.some((n) => n.title === "New taOS build available")).toBe(true);
  });
});
