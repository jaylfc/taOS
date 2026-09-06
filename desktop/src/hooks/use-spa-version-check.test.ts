import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

// Stub __TAOS_VERSION__ before importing the hook.
vi.stubGlobal("__TAOS_VERSION__", "1.0.0+abc123.def456");

import { useSpaVersionCheck } from "./use-spa-version-check";

function mockFetch(status: number, body: unknown) {
  vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok: status >= 200 && status < 300,
    json: () => Promise.resolve(body),
  } as Response);
}

/**
 * Flush microtasks + a zero-length timer tick so the initial check() call
 * (which is async) settles without running the 60 s interval forever.
 */
async function flushInitialCheck() {
  await act(() => vi.advanceTimersByTimeAsync(0));
}

describe("useSpaVersionCheck", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("__TAOS_VERSION__", "1.0.0+abc123.def456");
    Object.defineProperty(document, "visibilityState", {
      value: "visible",
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("returns hasNewBuild=false when version.json matches build version", async () => {
    mockFetch(200, { version: "1.0.0+abc123.def456" });

    const { result } = renderHook(() => useSpaVersionCheck());
    await flushInitialCheck();

    expect(result.current.hasNewBuild).toBe(false);
    expect(result.current.deployedVersion).toBe("1.0.0+abc123.def456");
  });

  it("returns hasNewBuild=true when deployed version differs", async () => {
    mockFetch(200, { version: "1.0.0+xyz789.aaa111" });

    const { result } = renderHook(() => useSpaVersionCheck());
    await flushInitialCheck();

    expect(result.current.hasNewBuild).toBe(true);
    expect(result.current.deployedVersion).toBe("1.0.0+xyz789.aaa111");
  });

  it("stays silent (hasNewBuild=false) for dev build versions", async () => {
    vi.stubGlobal("__TAOS_VERSION__", "dev");
    mockFetch(200, { version: "1.0.0+xyz789.aaa111" });

    const { result } = renderHook(() => useSpaVersionCheck());
    await flushInitialCheck();

    expect(result.current.hasNewBuild).toBe(false);
  });

  it("polls on visibilitychange to visible", async () => {
    mockFetch(200, { version: "1.0.0+abc123.def456" });

    const { result } = renderHook(() => useSpaVersionCheck());
    await flushInitialCheck();
    expect(result.current.deployedVersion).toBe("1.0.0+abc123.def456");

    // Simulate hidden → visible transition.
    vi.clearAllMocks();
    mockFetch(200, { version: "1.0.0+new.new.new" });

    Object.defineProperty(document, "visibilityState", {
      value: "hidden",
      writable: true,
      configurable: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));

    Object.defineProperty(document, "visibilityState", {
      value: "visible",
      writable: true,
      configurable: true,
    });
    await act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await flushInitialCheck();

    expect(result.current.deployedVersion).toBe("1.0.0+new.new.new");
    expect(result.current.hasNewBuild).toBe(true);
  });

  it("handles fetch errors gracefully", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network"));

    const { result } = renderHook(() => useSpaVersionCheck());
    await flushInitialCheck();

    expect(result.current.hasNewBuild).toBe(false);
    expect(result.current.deployedVersion).toBeNull();
  });

  it("handles non-ok response gracefully", async () => {
    mockFetch(404, {});

    const { result } = renderHook(() => useSpaVersionCheck());
    await flushInitialCheck();

    expect(result.current.hasNewBuild).toBe(false);
  });
});
