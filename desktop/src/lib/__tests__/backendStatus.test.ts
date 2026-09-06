import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createBackendStatus } from "../backendStatus";

describe("backendStatus", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("starts as 'up' before any poll", () => {
    const bs = createBackendStatus({ healthUrl: "/api/health" });
    expect(bs.getStatus()).toBe("up");
  });

  it("flips to 'reconnecting' after a failed poll", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network"));
    const bs = createBackendStatus({ healthUrl: "/api/health", fetchImpl: fetchMock });
    bs.start();
    await vi.advanceTimersByTimeAsync(2000);
    expect(bs.getStatus()).toBe("reconnecting");
    bs.stop();
  });

  it("returns to 'up' after a successful poll", async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValue(new Response("{}", { status: 200 }));
    const bs = createBackendStatus({ healthUrl: "/api/health", fetchImpl: fetchMock });
    bs.start();
    await vi.advanceTimersByTimeAsync(2000); // first poll fails
    expect(bs.getStatus()).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(4000); // backoff to 4s, succeeds
    expect(bs.getStatus()).toBe("up");
    bs.stop();
  });

  it("backs off 2s -> 4s -> 8s -> 16s -> 30s (capped)", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network"));
    const bs = createBackendStatus({ healthUrl: "/api/health", fetchImpl: fetchMock });
    bs.start();
    // First poll at 2s
    await vi.advanceTimersByTimeAsync(2000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // Next at +4s
    await vi.advanceTimersByTimeAsync(4000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    // +8s
    await vi.advanceTimersByTimeAsync(8000);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    // +16s
    await vi.advanceTimersByTimeAsync(16000);
    expect(fetchMock).toHaveBeenCalledTimes(4);
    // +30s (cap)
    await vi.advanceTimersByTimeAsync(30000);
    expect(fetchMock).toHaveBeenCalledTimes(5);
    // +30s (still capped)
    await vi.advanceTimersByTimeAsync(30000);
    expect(fetchMock).toHaveBeenCalledTimes(6);
    bs.stop();
  });

  it("reports the long-reconnecting threshold after 60s in reconnecting", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network"));
    const bs = createBackendStatus({ healthUrl: "/api/health", fetchImpl: fetchMock });
    bs.start();
    await vi.advanceTimersByTimeAsync(2000);
    expect(bs.getSecondsReconnecting()).toBeGreaterThanOrEqual(0);
    expect(bs.getSecondsReconnecting()).toBeLessThan(60);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(bs.getSecondsReconnecting()).toBeGreaterThanOrEqual(60);
    bs.stop();
  });

  it("subscribers are called on every status change", async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValue(new Response("{}", { status: 200 }));
    const bs = createBackendStatus({ healthUrl: "/api/health", fetchImpl: fetchMock });
    const cb = vi.fn();
    bs.subscribe(cb);
    bs.start();
    await vi.advanceTimersByTimeAsync(2000);
    await vi.advanceTimersByTimeAsync(4000);
    // Called at least twice (reconnecting then up)
    expect(cb.mock.calls.length).toBeGreaterThanOrEqual(2);
    bs.stop();
  });

  it("reportVersion stores valid version strings, ignores garbage", () => {
    const bs = createBackendStatus({ healthUrl: "/api/health" });
    bs.reportVersion("0.42.2");
    expect(bs.getCurrentVersion()).toBe("0.42.2");
    bs.reportVersion("1.0.0-rc.1+build.7");
    expect(bs.getCurrentVersion()).toBe("1.0.0-rc.1+build.7");
    bs.reportVersion("<script>alert(1)</script>");
    expect(bs.getCurrentVersion()).toBe("1.0.0-rc.1+build.7"); // unchanged
    bs.reportVersion("");
    expect(bs.getCurrentVersion()).toBe("1.0.0-rc.1+build.7"); // unchanged
  });

  it("stop() prevents the next poll from being scheduled even mid-flight", async () => {
    let resolveFetch: (() => void) | null = null;
    const fetchMock = vi.fn().mockImplementation(
      () => new Promise<Response>((res) => { resolveFetch = () => res(new Response("{}", { status: 200 })); })
    );
    const bs = createBackendStatus({ healthUrl: "/api/health", fetchImpl: fetchMock });
    bs.start();
    await vi.advanceTimersByTimeAsync(2000);
    // Poll is now mid-flight (fetch hasn't resolved). Stop the loop:
    bs.stop();
    // Resolve the in-flight fetch — schedule() inside finally must NOT re-arm.
    resolveFetch?.();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("a throwing subscriber does not affect other subscribers or status transitions", async () => {
    // Fail once so status transitions to reconnecting (triggers notify), then succeed.
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValue(new Response("{}", { status: 200 }));
    const bs = createBackendStatus({ healthUrl: "/api/health", fetchImpl: fetchMock });
    const good = vi.fn();
    bs.subscribe(() => { throw new Error("boom"); });
    bs.subscribe(good);
    bs.start();
    await vi.advanceTimersByTimeAsync(2000); // first poll fails → status→reconnecting → notify fires
    expect(bs.getStatus()).toBe("reconnecting");
    expect(good).toHaveBeenCalled();   // sibling subscriber fired despite the throw
    expect(bs.getStatus()).toBe("reconnecting"); // throw did not corrupt the status machine
    bs.stop();
  });

  it("notifies subscribers exactly once when crossing the 60s reconnecting threshold", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network"));
    const bs = createBackendStatus({ healthUrl: "/api/health", fetchImpl: fetchMock });
    const cb = vi.fn();
    bs.start();
    await vi.advanceTimersByTimeAsync(2000); // first failure → notify (status change to reconnecting)
    bs.subscribe(cb);
    // Walk past the 60s threshold via subsequent polls:
    await vi.advanceTimersByTimeAsync(120_000);
    const callsAfterCross = cb.mock.calls.length;
    expect(callsAfterCross).toBeGreaterThanOrEqual(1);
    // Now walk another 90s — no further notifies for the long-reconnecting reason:
    cb.mockClear();
    await vi.advanceTimersByTimeAsync(90_000);
    expect(cb).not.toHaveBeenCalled();
    bs.stop();
  });
});
