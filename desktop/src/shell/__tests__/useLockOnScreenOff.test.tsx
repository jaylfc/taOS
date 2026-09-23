import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useLockOnScreenOff } from "../useLockOnScreenOff";

class FakeEventSource {
  static last: FakeEventSource | null = null;
  url: string;
  listeners: Record<string, Array<() => void>> = {};
  closed = false;
  constructor(url: string) { this.url = url; FakeEventSource.last = this; }
  addEventListener(kind: string, fn: () => void) { (this.listeners[kind] ||= []).push(fn); }
  removeEventListener(kind: string, fn: () => void) {
    this.listeners[kind] = (this.listeners[kind] || []).filter((f) => f !== fn);
  }
  close() { this.closed = true; }
  fire(kind: string) { (this.listeners[kind] || []).forEach((f) => f()); }
}

describe("useLockOnScreenOff", () => {
  const replace = vi.fn();
  beforeEach(() => {
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}"))));
    Object.defineProperty(window, "location", { value: { replace }, configurable: true });
    replace.mockClear();
  });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("does nothing until the screen goes off", () => {
    renderHook(() => useLockOnScreenOff());
    expect(FakeEventSource.last?.url).toBe("/auth/lock-events");
    expect(fetch).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it("covers the screen black, locks, then goes to the lock screen", async () => {
    renderHook(() => useLockOnScreenOff());
    FakeEventSource.last!.fire("screen-off");
    // The cover is synchronous: it must be the last frame before the panel dies.
    const cover = document.body.lastElementChild as HTMLElement;
    expect(cover.style.background).toMatch(/#000|rgb\(0, 0, 0\)/);
    await vi.waitFor(() => expect(replace).toHaveBeenCalledWith("/auth/login"));
    expect(fetch).toHaveBeenCalledWith("/auth/lock", expect.objectContaining({ method: "POST" }));
  });

  it("a second screen-off while locking does not lock twice", async () => {
    renderHook(() => useLockOnScreenOff());
    FakeEventSource.last!.fire("screen-off");
    FakeEventSource.last!.fire("screen-off");
    await vi.waitFor(() => expect(replace).toHaveBeenCalledTimes(1));
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("closes its stream on unmount", () => {
    const { unmount } = renderHook(() => useLockOnScreenOff());
    unmount();
    expect(FakeEventSource.last?.closed).toBe(true);
  });
});
