import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useLockOnScreenOff } from "../useLockOnScreenOff";

class FakeEventSource {
  static last: FakeEventSource | null = null;
  listeners: Record<string, Array<() => void>> = {};
  constructor(_url: string) { FakeEventSource.last = this; }
  addEventListener(kind: string, fn: () => void) { (this.listeners[kind] ||= []).push(fn); }
  removeEventListener(kind: string, fn: () => void) {
    this.listeners[kind] = (this.listeners[kind] || []).filter((f) => f !== fn);
  }
  close() {}
  fire(kind: string) { (this.listeners[kind] || []).forEach((f) => f()); }
}

describe("useLockOnScreenOff cover", () => {
  const replace = vi.fn();
  beforeEach(() => {
    vi.stubGlobal("EventSource", FakeEventSource);
    Object.defineProperty(window, "location", { value: { replace }, configurable: true });
    replace.mockReset();
  });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("has the opaque aria-hidden cover in the document before the lock request", async () => {
    let coverAtRequest: HTMLElement | null = null;
    vi.stubGlobal("fetch", vi.fn(() => {
      coverAtRequest = document.body.lastElementChild as HTMLElement;
      return Promise.resolve(new Response("{}"));
    }));
    renderHook(() => useLockOnScreenOff());
    FakeEventSource.last!.fire("screen-off");
    await vi.waitFor(() => expect(replace).toHaveBeenCalled());
    expect(coverAtRequest).not.toBeNull();
    const cover = coverAtRequest as unknown as HTMLElement;
    expect(cover.getAttribute("aria-hidden")).toBe("true");
    expect(cover.style.background).toMatch(/#000|rgb\(0, 0, 0\)/);
    expect(cover.style.position).toBe("fixed");
    expect(Number(cover.style.zIndex)).toBeGreaterThanOrEqual(2147483647);
    expect(cover.tabIndex).toBe(-1);
    // No fade: it is black on the first frame, so there is nothing to reduce.
    expect(cover.style.transition).toBe("");
  });

  it("still goes to the lock screen when the lock request rejects", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
    renderHook(() => useLockOnScreenOff());
    FakeEventSource.last!.fire("screen-off");
    await vi.waitFor(() => expect(replace).toHaveBeenCalledWith("/auth/login"));
  });

  it("removes the cover when navigation does not happen", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}"))));
    replace.mockImplementation(() => { throw new Error("blocked"); });
    renderHook(() => useLockOnScreenOff());
    FakeEventSource.last!.fire("screen-off");
    await vi.waitFor(() => expect(replace).toHaveBeenCalled());
    await vi.waitFor(() => expect(document.body.querySelector("[aria-hidden=true]")).toBeNull());
  });

  it("takes the lock page title before the lock request, and restores it if the shell stays", async () => {
    document.title = "taOS";
    let titleAtRequest = "";
    vi.stubGlobal("fetch", vi.fn(() => {
      titleAtRequest = document.title;
      return Promise.resolve(new Response("{}"));
    }));
    replace.mockImplementation(() => { throw new Error("blocked"); });
    renderHook(() => useLockOnScreenOff());
    FakeEventSource.last!.fire("screen-off");
    await vi.waitFor(() => expect(replace).toHaveBeenCalled());
    expect(titleAtRequest).toBe("Sign in \u2014 taOS");
    await vi.waitFor(() => expect(document.title).toBe("taOS"));
  });
});
