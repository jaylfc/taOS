import { render, act } from "@testing-library/react";
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

  it("dispatches taos:backend-up and reloads exactly once when the backend recovers", async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const { BackendStatusProvider } = await import("../BackendStatusContext");
    const handler = vi.fn();
    window.addEventListener("taos:backend-up", handler);
    try {
      render(<BackendStatusProvider><div /></BackendStatusProvider>);

      // First probe fails -> reconnecting; no recovery signal yet.
      await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(handler).not.toHaveBeenCalled();
      expect(reload).not.toHaveBeenCalled();

      // Next probe succeeds -> recovery.
      await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
      expect(handler).toHaveBeenCalledTimes(1);
      expect(reload).toHaveBeenCalledTimes(1);

      // Further healthy probes do not re-fire.
      await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
      expect(handler).toHaveBeenCalledTimes(1);
      expect(reload).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener("taos:backend-up", handler);
    }
  });
});
