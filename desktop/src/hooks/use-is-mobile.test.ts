import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useIsMobile } from "./use-is-mobile";

function createMockMatchMedia(initialMatches: boolean) {
  let matches = initialMatches;
  const listeners: Array<(e: { matches: boolean }) => void> = [];
  return {
    get matches() { return matches; },
    set matches(v: boolean) { matches = v; },
    media: "(max-width: 767px)",
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn().mockImplementation(
      (_: string, cb: (e: { matches: boolean }) => void) => { listeners.push(cb); },
    ),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn().mockReturnValue(false),
    _fire(match: boolean) {
      matches = match;
      listeners.forEach((cb) => cb({ matches: match }));
    },
  } as unknown as MediaQueryList & { _fire: (m: boolean) => void };
}

describe("useIsMobile", () => {
  const originalMatchMedia = window.matchMedia;
  const originalInnerWidth = Object.getOwnPropertyDescriptor(window, "innerWidth");

  beforeEach(() => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      writable: true,
      value: 1024,
    });
  });

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
    if (originalInnerWidth) {
      Object.defineProperty(window, "innerWidth", originalInnerWidth);
    } else {
      delete (window as Record<string, unknown>)["innerWidth"];
    }
  });

  it("returns false on desktop viewport (width >= 768)", () => {
    window.matchMedia = vi.fn().mockReturnValue(createMockMatchMedia(false));
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });

  it("returns true on mobile viewport (width < 768)", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 400 });
    window.matchMedia = vi.fn().mockReturnValue(createMockMatchMedia(true));
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });

  it("updates when matchMedia change event fires", () => {
    const mql = createMockMatchMedia(false);
    window.matchMedia = vi.fn().mockReturnValue(mql);

    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);

    act(() => { (mql as unknown as { _fire: (m: boolean) => void })._fire(true); });
    expect(result.current).toBe(true);
  });
});
