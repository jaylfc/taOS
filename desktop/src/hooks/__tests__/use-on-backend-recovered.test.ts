import { act, renderHook } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

describe("useOnBackendRecovered", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.resetModules();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("calls callback when recoveredAt increments", async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValue(new Response("{}", { status: 200, headers: { "X-Taos-Version": "1.0.0" } }));
    vi.stubGlobal("fetch", fetchMock);

    const { BackendStatusProvider, useBackendStatus } = await import("@/contexts/BackendStatusContext");
    const { useOnBackendRecovered } = await import("@/hooks/use-on-backend-recovered");

    const callback = vi.fn();
    const { result } = renderHook(() => {
      useOnBackendRecovered(callback);
      return useBackendStatus();
    }, {
      wrapper: BackendStatusProvider,
    });

    // First probe fails -> reconnecting
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(callback).not.toHaveBeenCalled();
    expect(result.current.recoveredAt).toBe(0);

    // Next probe succeeds -> recovery
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
    expect(callback).toHaveBeenCalledTimes(1);
    expect(result.current.recoveredAt).toBe(1);

    // Further healthy probes do not re-fire
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(callback).toHaveBeenCalledTimes(1);
    expect(result.current.recoveredAt).toBe(1);
  });

  it("calls callback on each recovery cycle", async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValueOnce(new Response("{}", { status: 200, headers: { "X-Taos-Version": "1.0.0" } }))
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValue(new Response("{}", { status: 200, headers: { "X-Taos-Version": "1.0.1" } }));
    vi.stubGlobal("fetch", fetchMock);

    const { BackendStatusProvider, useBackendStatus } = await import("@/contexts/BackendStatusContext");
    const { useOnBackendRecovered } = await import("@/hooks/use-on-backend-recovered");

    const callback = vi.fn();
    const { result } = renderHook(() => {
      useOnBackendRecovered(callback);
      return useBackendStatus();
    }, {
      wrapper: BackendStatusProvider,
    });

    // First failure
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(callback).not.toHaveBeenCalled();

    // First recovery
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
    expect(callback).toHaveBeenCalledTimes(1);
    expect(result.current.recoveredAt).toBe(1);

    // Second failure
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(callback).toHaveBeenCalledTimes(1);

    // Second recovery
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
    expect(callback).toHaveBeenCalledTimes(2);
    expect(result.current.recoveredAt).toBe(2);
  });

  it("does not call callback on mount when already up", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValue(new Response("{}", { status: 200, headers: { "X-Taos-Version": "1.0.0" } }));
    vi.stubGlobal("fetch", fetchMock);

    const { BackendStatusProvider, useBackendStatus } = await import("@/contexts/BackendStatusContext");
    const { useOnBackendRecovered } = await import("@/hooks/use-on-backend-recovered");

    const callback = vi.fn();
    renderHook(() => {
      useOnBackendRecovered(callback);
      return useBackendStatus();
    }, {
      wrapper: BackendStatusProvider,
    });

    // Initial state is up, no recovery event should fire
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(callback).not.toHaveBeenCalled();
  });
});