import { describe, it, expect, vi, afterEach } from "vitest";
import { createTaosFetch, BackendUnavailableError } from "../taos-fetch";
import type { BackendStatusController } from "../backendStatus";

function makeStatusStub(initial: "up" | "reconnecting" | "down" = "up"): BackendStatusController {
  let status = initial;
  let version: string | null = null;
  return {
    getStatus: () => status,
    getCurrentVersion: () => version,
    getSecondsReconnecting: () => 0,
    reportVersion: vi.fn((v: string) => { version = v; }),
    subscribe: () => () => {},
    start: () => {},
    stop: () => {},
    // helper for tests
    _set: (s: typeof status) => { status = s; },
  } as unknown as BackendStatusController & { _set: (s: string) => void };
}

describe("taos-fetch", () => {
  afterEach(() => vi.restoreAllMocks());

  it("passes through and returns the response when status is 'up'", async () => {
    const stub = makeStatusStub("up");
    const inner = vi.fn().mockResolvedValue(
      new Response("ok", { status: 200, headers: { "X-Taos-Version": "0.1.0" } })
    );
    const f = createTaosFetch({ status: stub, fetchImpl: inner });
    const r = await f("/api/foo");
    expect(r.status).toBe(200);
    expect(inner).toHaveBeenCalledWith("/api/foo", undefined);
  });

  it("reports X-Taos-Version from response headers", async () => {
    const stub = makeStatusStub("up");
    const inner = vi.fn().mockResolvedValue(
      new Response("ok", { status: 200, headers: { "X-Taos-Version": "0.42.2" } })
    );
    const f = createTaosFetch({ status: stub, fetchImpl: inner });
    await f("/api/foo");
    expect(stub.reportVersion).toHaveBeenCalledWith("0.42.2");
  });

  it("does not call reportVersion if header is absent", async () => {
    const stub = makeStatusStub("up");
    const inner = vi.fn().mockResolvedValue(new Response("ok", { status: 200 }));
    const f = createTaosFetch({ status: stub, fetchImpl: inner });
    await f("/api/foo");
    expect(stub.reportVersion).not.toHaveBeenCalled();
  });

  it("throws BackendUnavailableError on network failure during reconnecting", async () => {
    const stub = makeStatusStub("reconnecting");
    const inner = vi.fn().mockRejectedValue(new TypeError("network down"));
    const f = createTaosFetch({ status: stub, fetchImpl: inner });
    await expect(f("/api/foo")).rejects.toBeInstanceOf(BackendUnavailableError);
  });

  it("rethrows the original error on network failure when status is 'up'", async () => {
    const stub = makeStatusStub("up");
    const original = new TypeError("network down");
    const inner = vi.fn().mockRejectedValue(original);
    const f = createTaosFetch({ status: stub, fetchImpl: inner });
    await expect(f("/api/foo")).rejects.toBe(original);
  });

  it("BackendUnavailableError carries name === 'BackendUnavailableError'", () => {
    const e = new BackendUnavailableError("test");
    expect(e.name).toBe("BackendUnavailableError");
    expect(e).toBeInstanceOf(Error);
  });
});
