import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import * as hook from "../useLockOnScreenOff";

class FakeEventSource {
  static last: FakeEventSource | null = null;
  listeners: Record<string, Array<() => void>> = {};
  constructor(_url: string) { FakeEventSource.last = this; }
  addEventListener(kind: string, fn: () => void) { (this.listeners[kind] ||= []).push(fn); }
  removeEventListener() {}
  close() {}
  fire(kind: string) { (this.listeners[kind] || []).forEach((f) => f()); }
}

const lockCalls = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter((c) => c[0] === "/auth/lock");

describe("useLockOnScreenOff confirms the lock", () => {
  const replace = vi.fn();
  beforeEach(() => {
    vi.stubGlobal("EventSource", FakeEventSource);
    Object.defineProperty(window, "location", { value: { replace }, configurable: true });
    replace.mockReset();
    document.cookie = "csrf_token=tok123";
    const delays = (hook as { LOCK_RETRY_DELAYS_MS?: number[] }).LOCK_RETRY_DELAYS_MS;
    if (delays) delays.fill(0);
  });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("sends the CSRF header on the lock request", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response("{}")));
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => hook.useLockOnScreenOff());
    FakeEventSource.last!.fire("screen-off");
    await vi.waitFor(() => expect(replace).toHaveBeenCalledWith("/auth/login"));
    const init = lockCalls(fetchMock)[0][1] as RequestInit;
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("tok123");
  });

  it("retries a rejected lock and goes to sign-in only after the server confirms", async () => {
    const answers = [new Response("{}", { status: 403 }), new Response("{}", { status: 200 })];
    const fetchMock = vi.fn(() => Promise.resolve(answers.shift() ?? new Response("{}")));
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => hook.useLockOnScreenOff());
    FakeEventSource.last!.fire("screen-off");
    await vi.waitFor(() => expect(replace).toHaveBeenCalledWith("/auth/login"));
    expect(lockCalls(fetchMock).length).toBe(2);
    expect(replace).toHaveBeenCalledTimes(1);
  });

  it("keeps the cover up when the server never confirms", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response("{}", { status: 503 })));
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => hook.useLockOnScreenOff());
    FakeEventSource.last!.fire("screen-off");
    await vi.waitFor(() => expect(lockCalls(fetchMock).length).toBeGreaterThan(1));
    await vi.waitFor(() => expect(replace).toHaveBeenCalled());
    expect(lockCalls(fetchMock).length).toBeGreaterThanOrEqual(5);
    expect((document.body.lastElementChild as HTMLElement).style.background).toMatch(/#000|rgb\(0, 0, 0\)/);
  });

  it("asks the server for a csrf cookie when none is set", async () => {
    document.cookie = "csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
    const fetchMock = vi.fn((url: string) => {
      if (url === "/auth/status") document.cookie = "csrf_token=fresh";
      return Promise.resolve(new Response("{}"));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => hook.useLockOnScreenOff());
    FakeEventSource.last!.fire("screen-off");
    await vi.waitFor(() => expect(replace).toHaveBeenCalled());
    const init = lockCalls(fetchMock as never)[0][1] as RequestInit;
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("fresh");
  });
});
