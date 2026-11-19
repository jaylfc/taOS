import { render, screen, act, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { BackendStatusProvider, useBackendStatus, __setBackendStatusSingletonForTest } from "../BackendStatusContext";
import { createBackendStatus } from "@/lib/backendStatus";

describe("BackendStatusProvider", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    __setBackendStatusSingletonForTest(null as any);
  });

  it("dispatches taos:backend-up event when backend recovers", async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValue(new Response("{}", { status: 200 }));

    // Create a test backend status with mock fetch
    const onUpMock = vi.fn(() => {
      window.dispatchEvent(new CustomEvent("taos:backend-up"));
    });
    const testBs = createBackendStatus({
      healthUrl: "/api/health",
      fetchImpl: fetchMock,
      onUp: onUpMock,
    });
    __setBackendStatusSingletonForTest(testBs);

    // Create a test component that uses the provider
    function TestComponent() {
      const { status } = useBackendStatus();
      return <div data-testid="status">{status}</div>;
    }

    const handler = vi.fn();
    window.addEventListener("taos:backend-up", handler);

    render(
      <BackendStatusProvider>
        <TestComponent />
      </BackendStatusProvider>
    );

    // Advance to trigger first poll failure -> reconnecting
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(screen.getByTestId("status").textContent).toBe("reconnecting");
    expect(handler).not.toHaveBeenCalled();

    // Advance to trigger success poll -> up
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });

    expect(screen.getByTestId("status").textContent).toBe("up");
    // Should have dispatched the event exactly once
    expect(handler).toHaveBeenCalledTimes(1);

    window.removeEventListener("taos:backend-up", handler);
  });
});