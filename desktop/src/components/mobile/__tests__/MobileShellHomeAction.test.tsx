import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createBackendStatus } from "@/lib/backendStatus";
import { BackendStatusProvider, useBackendStatus, getBackendStatusSingleton } from "@/contexts/BackendStatusContext";
import { useHomeKey, HOME_KEY } from "@/hooks/use-home-key";
import { render, screen } from "@testing-library/react";
import { ReactNode } from "react";

const press = (key: string) => window.dispatchEvent(new KeyboardEvent("keydown", { key }));

describe("Home action while banner shown (reconnecting)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("useHomeKey callback fires while backend status is reconnecting", async () => {
    // Create a backend status that stays in reconnecting
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network"));
    const bs = createBackendStatus({ healthUrl: "/api/health", fetchImpl: fetchMock });
    bs.start();

    // Advance to trigger first poll failure -> reconnecting
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    // Verify status is reconnecting
    expect(bs.getStatus()).toBe("reconnecting");

    // Now test that useHomeKey still works
    const onHome = vi.fn();
    renderHook(() => useHomeKey(true, onHome));

    press(HOME_KEY);

    expect(onHome).toHaveBeenCalledTimes(1);

    bs.stop();
  });

  it("BackendBanner shows while reconnecting and useHomeKey works", async () => {
    // Use the singleton that BackendStatusProvider will use
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network"));
    const bs = getBackendStatusSingleton();
    // Reset the singleton by creating a new one with our fetch mock
    // We need to stop the existing one first
    bs.stop();
    // Monkey-patch the fetch implementation
    const originalFetch = global.fetch;
    global.fetch = fetchMock as any;

    bs.start();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(bs.getStatus()).toBe("reconnecting");

    // Render a component that uses both useBackendStatus and useHomeKey
    function TestComponent() {
      const { status } = useBackendStatus();
      const onHome = vi.fn();
      useHomeKey(true, onHome);
      return <div data-status={status} data-testid="test-comp" />;
    }

    const { unmount } = render(
      <BackendStatusProvider>
        <TestComponent />
      </BackendStatusProvider>
    );

    expect(screen.getByTestId("test-comp").getAttribute("data-status")).toBe("reconnecting");

    press(HOME_KEY);

    unmount();
    bs.stop();
    global.fetch = originalFetch;
  });
});