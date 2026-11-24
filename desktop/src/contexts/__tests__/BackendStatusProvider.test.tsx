import { render, act } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// The real provider and its real page-lifetime singleton are used: nothing in
// this file replaces the singleton, so the test covers the production wiring
// of `onUp` in BackendStatusContext.
describe("BackendStatusProvider recovery wiring", () => {
  const reload = vi.fn();

  beforeEach(() => {
    vi.useFakeTimers();
    vi.resetModules(); // fresh singleton per test
    reload.mockReset();
    vi.stubGlobal("location", { ...window.location, reload });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("dispatches taos:backend-up, bumps recoveredAt, and never reloads when the backend recovers", async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValue(new Response("{}", { status: 200, headers: { "X-Taos-Version": "1.0.0" } }));
    vi.stubGlobal("fetch", fetchMock);

    const { BackendStatusProvider, useBackendStatus } = await import("../BackendStatusContext");
    const handler = vi.fn();
    window.addEventListener("taos:backend-up", handler);
    try {
      const { result } = renderHook(() => useBackendStatus(), {
        wrapper: BackendStatusProvider,
      });

      // First probe fails -> reconnecting; no recovery signal yet.
      await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(handler).not.toHaveBeenCalled();
      expect(reload).not.toHaveBeenCalled();
      expect(result.current.status).toBe("reconnecting");
      expect(result.current.recoveredAt).toBe(0);

      // Next probe succeeds -> recovery.
      await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
      expect(handler).toHaveBeenCalledTimes(1);
      expect(reload).not.toHaveBeenCalled();
      expect(result.current.status).toBe("up");
      expect(result.current.recoveredAt).toBe(1);

      // Further healthy probes do not re-fire.
      await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
      expect(handler).toHaveBeenCalledTimes(1);
      expect(reload).not.toHaveBeenCalled();
      expect(result.current.recoveredAt).toBe(1);
    } finally {
      window.removeEventListener("taos:backend-up", handler);
    }
  });

  it("increments recoveredAt on each recovery cycle", async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValueOnce(new Response("{}", { status: 200, headers: { "X-Taos-Version": "1.0.0" } }))
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValue(new Response("{}", { status: 200, headers: { "X-Taos-Version": "1.0.1" } }));
    vi.stubGlobal("fetch", fetchMock);

    const { BackendStatusProvider, useBackendStatus } = await import("../BackendStatusContext");
    const handler = vi.fn();
    window.addEventListener("taos:backend-up", handler);
    try {
      const { result } = renderHook(() => useBackendStatus(), {
        wrapper: BackendStatusProvider,
      });

      // First failure
      await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
      expect(result.current.status).toBe("reconnecting");
      expect(result.current.recoveredAt).toBe(0);

      // First recovery
      await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
      expect(result.current.status).toBe("up");
      expect(result.current.recoveredAt).toBe(1);

      // Second failure
      await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
      expect(result.current.status).toBe("reconnecting");
      expect(result.current.recoveredAt).toBe(1);

      // Second recovery
      await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
      expect(result.current.status).toBe("up");
      expect(result.current.recoveredAt).toBe(2);
      expect(handler).toHaveBeenCalledTimes(2);
      expect(reload).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener("taos:backend-up", handler);
    }
  });

  it("calls consumer loader exactly once on recovery and never reloads the page", async () => {
    // This test simulates an app that loads data on mount and uses
    // useOnBackendRecovered to re-fetch when the backend recovers.
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValue(new Response("{}", { status: 200, headers: { "X-Taos-Version": "1.0.0" } }));
    vi.stubGlobal("fetch", fetchMock);

    const { BackendStatusProvider, useBackendStatus } = await import("../BackendStatusContext");
    const { useOnBackendRecovered } = await import("@/hooks/use-on-backend-recovered");

    const load = vi.fn();
    function TestConsumer() {
      useOnBackendRecovered(load);
      const { recoveredAt } = useBackendStatus();
      return <div data-testid="consumer" data-recovered-at={recoveredAt} />;
    }

    render(<BackendStatusProvider><TestConsumer /></BackendStatusProvider>);

    // Wait for first probe to fail -> reconnecting
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(load).not.toHaveBeenCalled();

    // Next probe succeeds -> recovery
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });

    // The loader should have been called exactly once due to recovery
    expect(load).toHaveBeenCalledTimes(1);
    expect(reload).not.toHaveBeenCalled();
  });
});